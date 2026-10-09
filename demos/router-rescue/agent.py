"""router-rescue: ISP support that looks at your router through the camera.

The caller's internet is down. They point a camera at the router; the agent
looks only when the answer depends on what the router shows, reads the lights
from a three-frame strip (one frame cannot see a blink), and walks them
through the fix one step at a time.

Stack: Deepgram STT, OpenAI LLM and vision, Cartesia TTS.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py dev, then join the room from any LiveKit client that
   publishes a camera and a microphone (for example agents-playground.livekit.io).
"""

import asyncio
import base64
import io
import json
import logging
import time
from typing import Literal

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
)
from livekit.plugins import cartesia, deepgram, openai, silero
from openai import AsyncOpenAI
from PIL import Image

load_dotenv()

logger = logging.getLogger(__name__)

LIGHTS = ("power", "internet", "wifi", "lan")
STATES = (
    "off",
    "solid_green",
    "blinking_green",
    "solid_amber",
    "blinking_amber",
    "solid_red",
    "blinking_red",
    "solid_white",
    "blinking_white",
    "not_visible",
)
Focus = Literal["lights", "label", "cables"]

# Three frames this far apart catch a 1 to 2 Hz blink both on and off.
FRAMES = 3
FRAME_GAP = 0.35
MODEL_TILE = (320, 240)
THUMB_TILE = (160, 120)
VISION_MODEL = "gpt-4.1-mini"
MAX_LOOKS = 6
# A second look this soon at the same thing reuses the first one.
REUSE_SECONDS = 3.0

VISION_PROMPT = (
    "You read a home internet router through a caller's camera. The image is "
    f"{FRAMES} frames side by side, left to right, about {FRAME_GAP} seconds apart. "
    "Report each front light (power, internet, wifi, lan). A light that is on in "
    "some frames and off in others is blinking. Use not_visible when you cannot "
    "see a light; never guess. Copy any readable label text (model, network name) "
    "exactly, or leave it empty. Keep the note under 15 words."
)

LIGHT_SCHEMA = {"type": "string", "enum": list(STATES)}
VISION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "router_reading",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["router_visible", "lights", "label", "note"],
            "properties": {
                "router_visible": {"type": "boolean"},
                "lights": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(LIGHTS),
                    "properties": {light: LIGHT_SCHEMA for light in LIGHTS},
                },
                "label": {"type": "string"},
                "note": {"type": "string"},
            },
        },
    },
}

# The support playbook for the (fictional) Northline Hub 3. The model reads the
# lights; this table, not the model, decides what they mean and what to do.
DIAGNOSES = {
    "no_router": (
        "Router not in view",
        (
            "Ask them to hold the front of the router up to the camera, about an arm's "
            "length away, with the lights facing the lens."
        ),
    ),
    "no_power": (
        "No power",
        (
            "Check the power adapter is plugged into the router and the wall, then try "
            "another outlet."
        ),
    ),
    "hardware_fault": (
        "Hardware fault",
        (
            "A red power light means the router failed its self-test. Close the ticket as "
            "escalated: a replacement router ships next business day."
        ),
    ),
    "booting": (
        "Starting up",
        "The router is booting. Ask them to wait about a minute, then look again.",
    ),
    "no_line": (
        "Line unplugged",
        (
            "The internet light is off, so no signal reaches the router. Ask them to push "
            "the cable in the port marked WAN until it clicks."
        ),
    ),
    "no_sync": (
        "No sync with the network",
        (
            "Power cycle it: unplug the power for ten seconds, plug it back in, and wait "
            "for the internet light to turn solid."
        ),
    ),
    "outage": (
        "Network outage",
        (
            "A red internet light means the line is up but the network refused it. Close "
            "the ticket as escalated: the area outage team takes it from here."
        ),
    ),
    "wifi_off": (
        "Wi-Fi switched off",
        "Ask them to press and hold the Wi-Fi button on the side for three seconds.",
    ),
    "healthy": (
        "Router healthy",
        (
            "Every light is normal, so the router is fine. Suggest they forget the Wi-Fi "
            "network on their device and join it again."
        ),
    ),
    "unclear": (
        "Lights unclear",
        ("Ask them to move closer and dim any bright light behind the router, then look again."),
    ),
}


def diagnose(reading: dict) -> str:
    """Map a light reading to one playbook entry, most severe first."""
    if not reading.get("router_visible"):
        return "no_router"
    lights = reading.get("lights", {})
    power, internet, wifi = (lights.get(k, "not_visible") for k in ("power", "internet", "wifi"))
    if power == "off":
        return "no_power"
    if power.endswith("_red"):
        return "hardware_fault"
    if power.startswith("blinking"):
        return "booting"
    if internet == "off":
        return "no_line"
    if internet.endswith("_amber"):
        return "no_sync"
    if internet.endswith("_red"):
        return "outage"
    if wifi == "off":
        return "wifi_off"
    healthy = ("solid_green", "blinking_green", "solid_white", "blinking_white")
    if power in healthy and internet in healthy:
        return "healthy"
    return "unclear"


def label_of(state: str) -> str:
    return state.replace("_", " ")


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
    component_id: str | None = None,
) -> None:
    envelope = {"type": "ui_event", "component": component, "action": action, "props": props or {}}
    if component_id is not None:
        envelope["id"] = component_id
    try:
        payload = json.dumps(envelope).encode("utf-8")
    except (TypeError, ValueError):
        logger.exception("failed to encode playground ui event")
        return
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except RuntimeError:
        logger.exception("failed to schedule playground ui event")
        return

    def log_publish_failure(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("failed to publish playground ui event")

    task.add_done_callback(log_publish_failure)


def camera_track(room: rtc.Room) -> rtc.RemoteVideoTrack | None:
    for participant in room.remote_participants.values():
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            continue
        for publication in participant.track_publications.values():
            track = publication.track
            if isinstance(track, rtc.RemoteVideoTrack) and not publication.muted:
                return track
    return None


def _to_image(frame: rtc.VideoFrame) -> Image.Image:
    rgba = frame.convert(rtc.VideoBufferType.RGBA)
    return Image.frombytes("RGBA", (rgba.width, rgba.height), bytes(rgba.data)).convert("RGB")


def contact_strip(images: list[Image.Image], tile: tuple[int, int], quality: int) -> bytes:
    """Lay frames left to right in one JPEG, so one image carries the timing."""
    strip = Image.new("RGB", (tile[0] * len(images), tile[1]))
    for i, image in enumerate(images):
        fitted = image.copy()
        fitted.thumbnail(tile)
        x = i * tile[0] + (tile[0] - fitted.width) // 2
        strip.paste(fitted, (x, (tile[1] - fitted.height) // 2))
    out = io.BytesIO()
    strip.save(out, format="JPEG", quality=quality)
    return out.getvalue()


async def sample_frames(track: rtc.RemoteVideoTrack, timeout: float = 2.0) -> list[Image.Image]:
    """Open the camera stream only for this look and keep three spaced frames."""
    stream = rtc.VideoStream(track, capacity=1)
    images: list[Image.Image] = []
    next_at = 0.0
    try:
        async with asyncio.timeout(timeout + FRAMES * FRAME_GAP):
            async for event in stream:
                now = time.monotonic()
                if now < next_at:
                    continue
                images.append(_to_image(event.frame))
                if len(images) == FRAMES:
                    break
                next_at = now + FRAME_GAP
    except TimeoutError:
        pass
    finally:
        await stream.aclose()
    return images


class RouterRescue(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                "You are Nova, phone support for Northline Fibre, a home internet "
                "provider. The caller's internet is not working. You can see their "
                "router only by calling look_at_camera, and each look costs money, so "
                "look only when the answer depends on what the router shows right now: "
                "the first diagnosis, checking a step they just did, or reading the "
                "label. Never look for small talk or to repeat what you already saw. "
                "Before the first look, ask them to point the camera at the front "
                "lights. After a look, say in one sentence what you saw, then give "
                "exactly the next step from the tool result. When they say they did "
                "it, look again to confirm. When the lights are healthy, or the tool "
                "says to escalate, call close_ticket. Short plain sentences, no lists, "
                "no markdown."
            ),
        )
        self.room = room
        self._vision = AsyncOpenAI(max_retries=0, timeout=6)
        self._looks: list[dict] = []
        self._turns = 0
        self._ticket = "open"

    def meter_vision(self, usage) -> None:
        """Bill a vision request to this call; the hosted class overrides it."""

    def publish_initial(self) -> None:
        self._publish_stats()

    def _publish_stats(self) -> None:
        publish_ui_event(
            self.room,
            "RouterStats",
            "update",
            component_id="stats",
            props={"turns": self._turns, "looks": len(self._looks), "ticket": self._ticket},
        )

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        self._turns += 1
        self._publish_stats()

    async def read_router(self, strip: bytes, focus: str) -> dict:
        url = "data:image/jpeg;base64," + base64.b64encode(strip).decode()
        response = await self._vision.chat.completions.create(
            model=VISION_MODEL,
            store=False,
            max_completion_tokens=160,
            response_format=VISION_SCHEMA,
            messages=[
                {"role": "system", "content": VISION_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Focus on the {focus}."},
                        {"type": "image_url", "image_url": {"url": url, "detail": "low"}},
                    ],
                },
            ],
        )
        self.meter_vision(response.usage)
        reading = json.loads(response.choices[0].message.content)
        lights = reading.get("lights") or {}
        reading["lights"] = {
            k: lights.get(k) if lights.get(k) in STATES else "not_visible" for k in LIGHTS
        }
        reading["label"] = str(reading.get("label", ""))[:60]
        reading["note"] = str(reading.get("note", ""))[:120]
        return reading

    def _publish_look(self, look: dict, thumb: bytes | None) -> None:
        props = {k: v for k, v in look.items() if k != "at"}
        publish_ui_event(
            self.room, "RouterLook", "update", component_id=f"look-{look['n']}", props=props
        )
        if thumb:

            async def send() -> None:
                try:
                    writer = await self.room.local_participant.stream_bytes(
                        f"look-{look['n']}.jpg",
                        total_size=len(thumb),
                        mime_type="image/jpeg",
                        topic="router-frame",
                    )
                    await writer.write(thumb)
                    await writer.aclose()
                except Exception:
                    logger.exception("failed to send the look thumbnail")

            asyncio.create_task(send())

    def _summary(self, look: dict) -> str:
        title, step = DIAGNOSES[look["diagnosis"]]
        lights = ", ".join(f"{k} {label_of(v)}" for k, v in look["lights"].items())
        label = f" Label reads: {look['label']}." if look["label"] else ""
        return f"Saw: {lights}.{label} Diagnosis: {title}. Next step: {step}"

    @function_tool()
    async def look_at_camera(
        self, context: RunContext, reason: str, focus: Focus = "lights"
    ) -> str:
        """Look at the caller's router through their camera, once.

        Only call this when the answer depends on what the router shows now.

        reason: why you need to look, in a few words (shown to the caller).
        focus: lights, label, or cables.
        """
        recent = self._looks[-1] if self._looks else None
        if recent and recent["focus"] == focus and time.monotonic() - recent["at"] < REUSE_SECONDS:
            return "You looked a moment ago. " + self._summary(recent)
        if len(self._looks) >= MAX_LOOKS:
            return "Look limit reached for this call. Work from what you already saw."
        track = camera_track(self.room)
        if track is None:
            return "Their camera is off. Ask them to turn it on and point it at the router."
        context.disallow_interruptions()
        hold = context.session.say("One sec, looking.", add_to_chat_ctx=False)
        started = time.monotonic()
        images = await sample_frames(track)
        if len(images) < FRAMES:
            return "No picture came through. Ask them to check the camera is on."
        try:
            reading = await self.read_router(contact_strip(images, MODEL_TILE, 75), focus)
        except Exception:  # noqa: BLE001 - any vision failure becomes a spoken retry
            logger.warning("vision request failed")
            return "The picture could not be read. Ask them to hold still, then look again."
        look = {
            "n": len(self._looks) + 1,
            "reason": reason.strip()[:120],
            "focus": focus,
            "lights": reading["lights"],
            "label": reading["label"],
            "visible": bool(reading.get("router_visible")),
            "diagnosis": diagnose(reading),
            "ms": int((time.monotonic() - started) * 1000),
            "frames": FRAMES,
            "at": time.monotonic(),
        }
        self._looks.append(look)
        self._publish_look(look, contact_strip(images, THUMB_TILE, 70))
        self._publish_stats()
        if not hold.done():
            hold.interrupt()
        return self._summary(look)

    @function_tool()
    async def close_ticket(
        self, context: RunContext, outcome: Literal["fixed", "escalated"]
    ) -> str:
        """Close the support ticket once the router is healthy or must be escalated.

        outcome: fixed when the lights are healthy, escalated when the playbook says so.
        """
        self._ticket = outcome
        self._publish_stats()
        if outcome == "fixed":
            return "Ticket closed as fixed. Thank them and say goodbye."
        return "Ticket escalated. Tell them a specialist follows up, and say goodbye."

    async def on_exit(self) -> None:
        await self._vision.close()


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="router-rescue")
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-2"),
        vad=ctx.proc.userdata["vad"],
    )
    await ctx.connect()
    agent = RouterRescue(ctx.room)
    await session.start(agent=agent, room=ctx.room)
    agent.publish_initial()
    await session.generate_reply(
        instructions="Greet them as Northline Fibre support and ask what is going on with their internet."
    )


if __name__ == "__main__":
    cli.run_app(server)
