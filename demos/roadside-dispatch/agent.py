# demos/roadside-dispatch/agent.py
"""Roadside dispatch voice agent with live Tyto audio-health scoring.

A roadside assistance dispatcher takes a breakdown call, captures the dispatch
details (location, vehicle, plate, callback), and dispatches help. Tyto scores
the caller's incoming audio on a rolling five-second window; the agent mirrors
the scores onto the playground, adapts when audio degrades, and gates field
accuracy so a field captured over a bad line is re-confirmed before dispatch.

Run it:
1. Copy .env.example to .env and fill all seven keys (AIC_SDK_LICENSE from
   developers.ai-coustics.com).
2. Run uv sync.
3. Run uv run --no-project python agent.py download-files.
4. Run uv run --no-project python agent.py dev, then open
   https://playground.mahimai.ca/demos/roadside-dispatch.

Recording: coming after the demo is recorded.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Literal

import aic_sdk as aic
import numpy as np
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
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from health import (
    DIMENSIONS,
    NEUTRAL,
    AudioHealth,
    band,
)

load_dotenv()
logger = logging.getLogger(__name__)

# --- Call-lifecycle limits (every tunable in one place) ---
MAX_CALL_SECONDS = 300
IDLE_HANGUP_SECONDS = 30

# --- Tyto / scoring config (every tunable in one place) ---
TYTO_MODEL = "tyto-l-16khz"
MODEL_DIR = Path(__file__).parent / "models"
WINDOW_SECONDS = 5  # Tyto's fixed analysis window
HOP_SECONDS = 1  # score this often
CRITICAL_FIELDS = ("location", "vehicle", "plate", "callback")

DIM_LABELS = {
    "noise": "noise",
    "speaker_reverb": "reverb",
    "speaker_loudness": "loudness",
    "interfering_speech": "interfering speech",
    "media_speech": "background media",
    "packet_loss": "packet loss",
}

INTERVENTION_LINES = {
    "noise": "It is getting noisy on your end. I will speak up and confirm the key details.",
    "media_speech": "I can hear a radio in the background. Could you turn it down so I get this right?",
    "interfering_speech": "There are other voices coming through. I will focus on just you now.",
    "packet_loss": "The line is breaking up. Let me confirm the important details as we go.",
    "speaker_reverb": "You sound far away. Could you move closer to the phone or out from the overpass?",
}

VERDICTS = {
    "noise": "noise is high",
    "media_speech": "background media detected",
    "interfering_speech": "other voices detected",
    "packet_loss": "line is dropping",
    "speaker_reverb": "caller sounds far away",
}


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
    component_id: str | None = None,
) -> None:
    """Publish a playground UI event; see protocol.ts and demo component registries."""
    envelope = {
        "type": "ui_event",
        "component": component,
        "action": action,
        "props": props or {},
    }
    if component_id is not None:
        envelope["id"] = component_id

    try:
        payload = json.dumps(envelope).encode("utf-8")
    except (TypeError, ValueError):
        logger.exception("failed to encode playground ui event")
        return

    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(
                payload,
                topic="ui",
                reliable=True,
            )
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


def _ui_action(room: rtc.Room, component_id: str) -> Literal["mount", "update"]:
    mounted = getattr(room, "_awesome_voice_ui_mounted", None)
    if mounted is None:
        mounted = set()
        setattr(room, "_awesome_voice_ui_mounted", mounted)

    if component_id in mounted:
        return "update"

    mounted.add(component_id)
    return "mount"


def _verdict(h: AudioHealth) -> str:
    if h.band == "good":
        return "audio is clean"
    drv = h.driver()
    return (
        VERDICTS.get(drv, "audio quality degraded") if drv else "audio quality degraded"
    )


def _publish_health(room: rtc.Room, h: AudioHealth) -> None:
    publish_ui_event(
        room,
        "Health",
        _ui_action(room, "health"),
        component_id="health",
        props={"risk": round(h.risk or 0.0, 3), "band": h.band},
    )


def _publish_risk(room: rtc.Room, h: AudioHealth) -> None:
    publish_ui_event(
        room,
        "Stat",
        _ui_action(room, "risk"),
        component_id="risk",
        props={
            "label": "tyto risk",
            "value": f"{h.risk or 0.0:.2f}",
            "caption": _verdict(h),
        },
    )


def _publish_meters(room: rtc.Room, h: AudioHealth) -> None:
    driver = h.driver()
    items = []
    for d in DIMENSIONS:
        value = round(h.dims.get(d, 0.0), 3)
        item: dict = {"label": DIM_LABELS[d], "value": value}
        if d == NEUTRAL:
            item["neutral"] = True
        else:
            item["band"] = band(value)
            if d == driver:
                item["driver"] = True
        items.append(item)
    publish_ui_event(
        room,
        "Meters",
        _ui_action(room, "meters"),
        component_id="meters",
        props={"title": "audio health", "items": items},
    )


def _publish_verdict(room: rtc.Room, h: AudioHealth) -> None:
    publish_ui_event(
        room,
        "Card",
        _ui_action(room, "verdict"),
        component_id="verdict",
        props={"title": "status", "body": _verdict(h), "accent": h.band != "good"},
    )


def _publish_details(room: rtc.Room, fields: dict[str, dict]) -> None:
    state_label = {
        "clean": "clean",
        "needs_confirmation": "unconfirmed",
        "confirmed": "confirmed",
    }
    items = [
        {"title": name, "subtitle": str(f["value"]), "right": state_label[f["state"]]}
        for name, f in fields.items()
    ]
    publish_ui_event(
        room,
        "List",
        _ui_action(room, "details"),
        component_id="details",
        props={"title": "captured details", "items": items},
    )


def _publish_warming(room: rtc.Room) -> None:
    publish_ui_event(
        room,
        "Stat",
        _ui_action(room, "risk"),
        component_id="risk",
        props={"label": "tyto risk", "value": "··", "caption": "warming up"},
    )
    publish_ui_event(
        room,
        "Health",
        _ui_action(room, "health"),
        component_id="health",
        props={"risk": 0.0, "band": "good"},
    )


# confirmed against LiveKit docs: track_subscribed emits (track, publication, participant)
# in livekit-python 1.x; the *_args absorbs publication and participant.
async def _wait_for_caller_track(room: rtc.Room) -> rtc.Track:
    """Return the caller's audio track, waiting for it if not yet subscribed."""
    for participant in room.remote_participants.values():
        for pub in participant.track_publications.values():
            if pub.track and pub.track.kind == rtc.TrackKind.KIND_AUDIO:
                return pub.track

    fut: asyncio.Future[rtc.Track] = asyncio.get_event_loop().create_future()

    def _on_subscribed(track: rtc.Track, *_args) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO and not fut.done():
            fut.set_result(track)

    room.on("track_subscribed", _on_subscribed)
    try:
        return await fut
    finally:
        room.off("track_subscribed", _on_subscribed)


async def _score_loop(
    room: rtc.Room,
    analyzer: "aic.FileAnalyzer",
    health: AudioHealth,
    on_window,
) -> None:
    """Tap the caller's audio, score a rolling window with Tyto, drive UI and interventions.

    Buffers mono float32 samples at the track's native rate. AudioStream defaults to
    num_channels=1 (auto-downmix) and sample_rate=48000. FileAnalyzer.analyze resamples
    to Tyto's 16 kHz internally, so we do not resample here; we only keep a five-second
    window and score once per hop.

    confirmed against LiveKit docs: AudioStream is constructed with the Track object and
    yields AudioFrameEvent objects; frame fields accessed are .data, .sample_rate,
    .num_channels (all confirmed present on livekit.rtc.AudioFrame in 1.x).
    """
    # Re-acquire the caller's track whenever it changes. Replacing the mic (for
    # example the playground's road-noise mixer republishes it) ends the current
    # AudioStream; without re-acquiring, the loop would keep reading the dead
    # track and report no speaker (loudness, noise, reverb all near zero).
    prev_track: rtc.Track | None = None
    while True:
        track = await _wait_for_caller_track(room)
        if track is prev_track:
            break  # same track returned, nothing new to read
        prev_track = track
        logger.info("score loop reading caller audio track")

        # AudioStream auto-downmixes to mono (num_channels=1 default).
        stream = rtc.AudioStream(track, num_channels=1)
        buffer: list[float] = []
        since_hop = 0
        sample_rate: int | None = None

        async for event in stream:
            frame = event.frame
            sample_rate = frame.sample_rate
            samples = (
                np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
            )
            if frame.num_channels > 1:
                samples = samples.reshape(-1, frame.num_channels).mean(axis=1)

            buffer.extend(samples.tolist())
            since_hop += len(samples)

            window = int(WINDOW_SECONDS * sample_rate)
            hop = int(HOP_SECONDS * sample_rate)
            if len(buffer) > window:
                del buffer[: len(buffer) - window]

            if len(buffer) >= window and since_hop >= hop:
                since_hop = 0
                chunk = np.asarray(buffer, dtype=np.float32)
                results = await asyncio.to_thread(
                    analyzer.analyze, chunk, sample_rate, len(chunk)
                )
                if not results:
                    continue
                result = results[-1]
                raw = {
                    name: float(getattr(result, name))
                    for name in ("risk_score", *DIMENSIONS)
                }
                health.update(raw)
                _publish_health(room, health)
                _publish_risk(room, health)
                _publish_meters(room, health)
                _publish_verdict(room, health)
                await on_window()

        logger.info("caller audio track ended; re-acquiring")


class RoadsideAgent(Agent):
    def __init__(self, room: rtc.Room, health: AudioHealth) -> None:
        super().__init__(
            instructions=(
                "You are a calm roadside assistance dispatcher. A driver has "
                "broken down and is calling for help. Collect their location, "
                "their vehicle make model and color, their license plate, and a "
                "callback number, then dispatch. Capture each detail with its "
                "tool as soon as you hear it. If a tool says the line is rough, "
                "read the value back and confirm it before moving on, then call "
                "confirm_field. Do not dispatch until every detail is confirmed. "
                "After dispatch the call ends automatically. "
                "Keep replies short, plain text, no markdown or emojis."
            ),
        )
        self.room = room
        self.health = health
        self.fields: dict[str, dict] = {}
        self.dispatched: bool = False

    def _capture(self, name: str, value: str) -> str:
        state = self.health.field_state()  # clean | needs_confirmation
        self.fields[name] = {"value": value, "state": state}
        _publish_details(self.room, self.fields)
        return state

    @function_tool()
    async def set_location(self, context: RunContext, location: str) -> str:
        """Record where the caller is (road, mile marker, exit, landmark)."""
        if self._capture("location", location) == "needs_confirmation":
            return (
                f"The line is rough. I have your location as {location}. Is that right?"
            )
        return f"Got it, location {location}."

    @function_tool()
    async def set_vehicle(self, context: RunContext, vehicle: str) -> str:
        """Record the vehicle make, model, and color."""
        if self._capture("vehicle", vehicle) == "needs_confirmation":
            return f"The line is rough. I have a {vehicle}. Did I get that right?"
        return f"Got it, a {vehicle}."

    @function_tool()
    async def set_plate(self, context: RunContext, plate: str) -> str:
        """Record the license plate."""
        if self._capture("plate", plate) == "needs_confirmation":
            return f"The line is breaking up. I have plate {plate}. Can you confirm it?"
        return f"Got it, plate {plate}."

    @function_tool()
    async def set_callback(self, context: RunContext, number: str) -> str:
        """Record a callback phone number."""
        if self._capture("callback", number) == "needs_confirmation":
            return (
                f"The line is rough. I have your number as {number}. Is that correct?"
            )
        return f"Got it, I will call back on {number}."

    @function_tool()
    async def confirm_field(self, context: RunContext, name: str) -> str:
        """Mark a previously captured field confirmed after reading it back."""
        f = self.fields.get(name)
        if not f:
            return f"I do not have your {name} yet."
        f["state"] = "confirmed"
        _publish_details(self.room, self.fields)
        return f"Thanks, {name} confirmed."

    @function_tool()
    async def dispatch(self, context: RunContext) -> str:
        """Dispatch help once every critical field is captured and confirmed."""
        missing = [n for n in CRITICAL_FIELDS if n not in self.fields]
        if missing:
            return f"I still need your {', '.join(missing)} before I can send help."
        unconfirmed = [
            n
            for n in CRITICAL_FIELDS
            if self.fields[n]["state"] == "needs_confirmation"
        ]
        if unconfirmed:
            return f"The line was rough. Let me confirm your {', '.join(unconfirmed)} first."
        _publish_details(self.room, self.fields)
        self.dispatched = True
        return "Help is on the way. Stay safe and stand clear of traffic."


# confirmed against LiveKit docs: in livekit-agents 1.6, session.options.allow_interruptions
# is not a settable attribute (it is deprecated in the constructor and not stored directly).
# The live path is session.options.turn_handling["interruption"]["enabled"], which is a
# TypedDict and is mutable at runtime. The helper is defensive against missing keys or a
# version that does not expose the nested structure.
def _set_barge_in(session: AgentSession, *, enabled: bool) -> None:
    """Toggle barge-in (interruption handling) on a running AgentSession."""
    try:
        session.options.turn_handling["interruption"]["enabled"] = enabled
    except (AttributeError, KeyError, TypeError):
        logger.warning("could not toggle barge-in on this livekit-agents version")


def _make_on_window(session: AgentSession, agent: "RoadsideAgent", health: AudioHealth):
    async def on_window() -> None:
        for dim in health.newly_fired():
            if dim == "interfering_speech":
                _set_barge_in(session, enabled=False)
            publish_ui_event(
                agent.room,
                "Card",
                _ui_action(agent.room, "verdict"),
                component_id="verdict",
                props={
                    "title": "intervention",
                    "body": f"{DIM_LABELS[dim]}: {VERDICTS[dim]}",
                    "accent": True,
                },
            )
            await session.say(INTERVENTION_LINES[dim])
        for dim in health.newly_cleared():
            if dim == "interfering_speech":
                _set_barge_in(session, enabled=True)

    return on_window


def _watchdog_done_callback(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.exception("watchdog task failed", exc_info=exc)


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()
    MODEL_DIR.mkdir(exist_ok=True)
    model_path = aic.Model.download(TYTO_MODEL, MODEL_DIR)
    proc.userdata["tyto_model"] = aic.Model.from_file(model_path)


server.setup_fnc = prewarm


@server.rtc_session(agent_name="roadside-dispatch")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    health = AudioHealth()
    agent = RoadsideAgent(ctx.room, health)
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-2"),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )

    analyzer = aic.FileAnalyzer(
        ctx.proc.userdata["tyto_model"], os.environ["AIC_SDK_LICENSE"]
    )

    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()
    _publish_warming(ctx.room)

    score_task = asyncio.create_task(
        _score_loop(ctx.room, analyzer, health, _make_on_window(session, agent, health))
    )
    score_task.add_done_callback(
        lambda t: (
            t.cancelled()
            or (
                t.exception()
                and logger.exception("score loop failed", exc_info=t.exception())
            )
        )
    )

    # --- Call-lifecycle management ---
    #
    # confirmed against livekit-agents 1.6 source:
    # - session.say(text) returns SpeechHandle which is directly awaitable
    #   (SpeechHandle.__await__ -> wait_for_playout); awaiting it blocks
    #   until the TTS audio has fully played out.
    # - session.aclose() is the public graceful-shutdown API
    #   (internally CloseReason.USER_INITIATED).
    # - "agent_state_changed" fires on every agent-state transition
    #   (initializing/idle/listening/thinking/speaking); new_state=="idle"
    #   after dispatch means the closing line has finished playing.
    # - "user_input_transcribed" fires for every STT chunk
    #   (UserInputTranscribedEvent with is_final flag); used to reset the
    #   idle timer on any user speech activity.

    closed_event = asyncio.Event()
    idle_task_holder: list[asyncio.Task] = []

    async def _graceful_end(line: str) -> None:
        """Speak line (if any), end the call for everyone, then close, once."""
        if closed_event.is_set():
            return
        closed_event.set()
        if line:
            try:
                await session.say(line, allow_interruptions=False)
            except Exception:
                logger.exception("lifecycle say() failed")
        # Delete the room so the caller is disconnected too (the playground then
        # shows the session as ended), then close the agent session.
        try:
            await ctx.delete_room()
        except Exception:
            logger.exception("delete_room failed")
        await session.aclose()

    def _on_agent_state_changed(ev) -> None:
        # Close the session once the post-dispatch speech finishes.
        # Guard on old_state=="speaking" so a thinking->idle micro-transition
        # between the tool call and the LLM turn does not fire early.
        if (
            agent.dispatched
            and ev.old_state == "speaking"
            and ev.new_state == "idle"
            and not closed_event.is_set()
        ):
            asyncio.create_task(
                _graceful_end(""),
                name="dispatch_close",
            )

    session.on("agent_state_changed", _on_agent_state_changed)

    async def _idle_watchdog() -> None:
        try:
            await asyncio.sleep(IDLE_HANGUP_SECONDS)
        except asyncio.CancelledError:
            return
        if closed_event.is_set():
            return
        logger.info("idle timeout reached, ending session")
        await _graceful_end("Are you still there? I will close the call now.")

    async def _max_call_watchdog() -> None:
        try:
            await asyncio.sleep(MAX_CALL_SECONDS)
        except asyncio.CancelledError:
            return
        if closed_event.is_set():
            return
        logger.info("max call limit reached, ending session")
        await _graceful_end("We have reached the time limit for this call. Take care.")

    def _on_user_input(ev) -> None:
        # Reset the idle watchdog on every user speech event (final or interim).
        # confirmed: "user_input_transcribed" is emitted by
        # AgentSession._user_input_transcribed for every STT chunk.
        if closed_event.is_set():
            return
        if idle_task_holder and not idle_task_holder[0].done():
            idle_task_holder[0].cancel()
        t = asyncio.create_task(_idle_watchdog(), name="idle_watchdog")
        t.add_done_callback(_watchdog_done_callback)
        if idle_task_holder:
            idle_task_holder[0] = t
        else:
            idle_task_holder.append(t)

    session.on("user_input_transcribed", _on_user_input)

    def _cancel_watchdogs(_ev=None) -> None:
        if idle_task_holder and not idle_task_holder[0].done():
            idle_task_holder[0].cancel()
        if not max_task.done():
            max_task.cancel()
        if not score_task.done():
            score_task.cancel()

    session.on("close", _cancel_watchdogs)

    idle_t = asyncio.create_task(_idle_watchdog(), name="idle_watchdog")
    idle_t.add_done_callback(_watchdog_done_callback)
    idle_task_holder.append(idle_t)

    max_task = asyncio.create_task(_max_call_watchdog(), name="max_call_watchdog")
    max_task.add_done_callback(_watchdog_done_callback)

    await session.generate_reply(
        instructions="Greet the caller as roadside assistance and ask where they are and what happened."
    )


if __name__ == "__main__":
    cli.run_app(server)
