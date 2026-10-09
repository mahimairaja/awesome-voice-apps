"""flight-rebooking: a cancelled-flight rebooking line that keeps talking.

The caller's flight is cancelled. The agent searches for new flights in a
background tool, so it keeps answering questions (seat, bags, meals) while the
search runs. The search also sets off a simulated provider outage: the primary
LLM fails, a fallback model answers, and the primary comes back once a recovery
probe passes. Say "knock out the voice" to fail the primary TTS the same way.

Stack: Deepgram Nova-3 STT, OpenAI gpt-4o-mini (fallback gpt-4.1-nano),
Cartesia Sonic-2 TTS (fallback OpenAI tts-1). The airline is fictional.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
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
    APIConnectionError,
    BackgroundAudioPlayer,
    BuiltinAudioClip,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    llm,
    tts,
)
from livekit.agents.voice.background_audio import AudioConfig
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger(__name__)

AIRLINE = "Borealis Air"
# Simulated inventory lookup; a real GDS or airline API takes this long or longer.
SEARCH_SECONDS = 8.0
# How long a simulated outage keeps a primary provider failing.
OUTAGE_SECONDS = 15.0
MAX_EVENTS = 14

PRIMARY = {"llm": "gpt-4o-mini", "tts": "Cartesia Sonic-2"}
BACKUP = {"llm": "gpt-4.1-nano", "tts": "OpenAI tts-1"}

BOOKING = {
    "ref": "K7Q2PX",
    "flight": "Borealis 412",
    "route": "Toronto YYZ to Vancouver YVR",
    "depart": "Today 18:05",
    "seat": "14A window",
}

OPTIONS = [
    {
        "id": "A",
        "flight": "Borealis 418",
        "depart": "Tonight 21:40",
        "arrive": "23:59",
        "stops": "Nonstop",
        "seat": "15A window",
    },
    {
        "id": "B",
        "flight": "Borealis 420",
        "depart": "Tonight 20:15",
        "arrive": "00:50",
        "stops": "1 stop, Calgary",
        "seat": "22C aisle",
    },
    {
        "id": "C",
        "flight": "Borealis 102",
        "depart": "Tomorrow 07:10",
        "arrive": "09:25",
        "stops": "Nonstop",
        "seat": "14A window",
    },
]

POLICY = {
    "seat": "Seats are reassigned automatically. Option A has window 15A, option C keeps 14A.",
    "bags": "Checked bags move to the new flight automatically. No need to collect them.",
    "meal": "A 25 dollar meal voucher goes to the Borealis app for any delay over three hours.",
    "hotel": "Choosing the morning flight includes one night at the airport hotel.",
    "refund": "If no option works, a full refund goes back to the original payment.",
}

INSTRUCTIONS = (
    f"You are the {AIRLINE} rebooking line. The caller's flight, {BOOKING['flight']} "
    f"from {BOOKING['route']} at 18:05 today, was cancelled for weather. Booking "
    f"reference {BOOKING['ref']}, seat {BOOKING['seat']}. As soon as the caller wants a "
    "new flight, call search_flights. The search runs in the background: keep talking "
    "while it runs and answer questions about seats, bags, meals, hotels or refunds "
    "with check_policy. Never invent flights; offer only what search_flights returns, "
    "as option A, B or C, and call rebook only after the caller picks one. If the "
    "caller asks you to knock out the voice or the model, or to simulate storm "
    "traffic, call simulate_outage. Keep replies to one or two short sentences, plain "
    "text, no markdown, no emojis."
)


class Outage:
    """Injected provider failures for one call. Nothing here touches the network."""

    def __init__(self) -> None:
        self.until = {"llm": 0.0, "tts": 0.0}

    def start(self, part: str, seconds: float = OUTAGE_SECONDS) -> None:
        self.until[part] = time.monotonic() + seconds

    def down(self, part: str) -> bool:
        return time.monotonic() < self.until[part]

    def check(self, part: str) -> None:
        if self.down(part):
            raise APIConnectionError(f"simulated {part} outage", retryable=False)


class FlakyLLM(openai.LLM):
    """The primary model, failing fast while its simulated outage lasts."""

    def __init__(self, *, outage: Outage, **kwargs) -> None:
        super().__init__(**kwargs)
        self._outage = outage

    def chat(self, **kwargs):
        self._outage.check("llm")
        return super().chat(**kwargs)


class FlakyTTS(cartesia.TTS):
    """The primary voice, failing fast while its simulated outage lasts."""

    def __init__(self, *, outage: Outage, **kwargs) -> None:
        super().__init__(**kwargs)
        self._outage = outage

    def stream(self, **kwargs):
        self._outage.check("tts")
        return super().stream(**kwargs)

    def synthesize(self, text, **kwargs):
        self._outage.check("tts")
        return super().synthesize(text, **kwargs)


def build_llm(outage: Outage, **kwargs) -> llm.FallbackAdapter:
    # Not sticky: once the primary passes a recovery probe it serves again.
    return llm.FallbackAdapter(
        [
            FlakyLLM(outage=outage, model=PRIMARY["llm"], **kwargs),
            openai.LLM(model=BACKUP["llm"], **kwargs),
        ],
        attempt_timeout=4.0,
    )


def build_tts(outage: Outage) -> tts.FallbackAdapter:
    return tts.FallbackAdapter(
        [FlakyTTS(outage=outage, model="sonic-2"), openai.TTS(model="tts-1", voice="alloy")],
        max_retry_per_tts=0,
    )


def new_state() -> dict:
    return {
        "booking": dict(BOOKING, status="cancelled"),
        "search": "idle",
        "options": [],
        "rebooked": None,
        "providers": {
            part: {"primary": PRIMARY[part], "backup": BACKUP[part], "serving": "primary"}
            for part in ("llm", "tts")
        },
        "events": [],
    }


def publish_ui_event(room: rtc.Room, component: str, props: dict) -> None:
    envelope = {"type": "ui_event", "component": component, "action": "update", "props": props}
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(
                json.dumps(envelope).encode(), topic="ui", reliable=True
            )
        )
    except RuntimeError:
        logger.exception("failed to schedule ui event")
        return
    task.add_done_callback(_log_publish_failure)


def _log_publish_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.warning("failed to publish ui event")


class FlightRebooker(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self.state = new_state()
        self.outage = Outage()
        self.background: BackgroundAudioPlayer | None = None
        self._started = time.monotonic()

    def publish(self) -> None:
        publish_ui_event(self.room, "Rebooking", self.state)

    def log(self, lane: str, text: str) -> None:
        at = round(time.monotonic() - self._started, 1)
        self.state["events"] = [*self.state["events"], {"at": at, "lane": lane, "text": text}][
            -MAX_EVENTS:
        ]
        self.publish()

    def watch(self, llm_adapter: llm.FallbackAdapter, tts_adapter: tts.FallbackAdapter) -> None:
        """Turn the adapters' availability events into timeline rows."""

        def changed(part: str, instances: list, instance, available: bool) -> None:
            primary = instances.index(instance) == 0
            name = (PRIMARY if primary else BACKUP)[part]
            if not primary:
                self.log(part, f"{name} {'is back' if available else 'also failed'}")
                return
            self.state["providers"][part]["serving"] = "primary" if available else "backup"
            if available:
                self.log(part, f"{name} passed a recovery probe and serves again")
            else:
                self.log(part, f"{name} failed. {BACKUP[part]} took the next request")

        llm_adapter.on(
            "llm_availability_changed",
            lambda ev: changed("llm", llm_adapter._llm_instances, ev.llm, ev.available),
        )
        tts_adapter.on(
            "tts_availability_changed",
            lambda ev: changed("tts", tts_adapter._tts_instances, ev.tts, ev.available),
        )

    async def start_background(self, session: AgentSession) -> None:
        # Typing sounds while the LLM thinks, so a slow turn never sounds like dead air.
        self.background = BackgroundAudioPlayer(
            thinking_sound=[AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.5)]
        )
        await self.background.start(room=self.room, agent_session=session)
        session.on("close", lambda _: asyncio.create_task(self.background.aclose()))

    def begin_outage(self, part: str, reason: str) -> None:
        self.outage.start(part)
        self.log(part, f"{reason}: {PRIMARY[part]} starts failing")

    @function_tool(on_duplicate="reject")
    async def search_flights(
        self, ctx: RunContext, preference: Literal["earliest", "nonstop", "any"] = "any"
    ) -> str:
        """Search for replacement flights. Runs in the background for several seconds."""
        if self.state["rebooked"]:
            return "The caller is already rebooked."
        if self.state["options"]:
            return self._options_text()
        self.state["search"] = "running"
        self.state["booking"]["status"] = "searching"
        self.log("tool", "search_flights started in the background")
        await ctx.update(
            "Searching every flight to Vancouver tonight and tomorrow. Takes a few seconds."
        )
        # Storm traffic: the search coincides with the primary model failing.
        if not self.outage.down("llm"):
            self.begin_outage("llm", "Storm traffic")
        hold = None
        if self.background:
            hold = self.background.play(
                AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.35), loop=True
            )
        try:
            async with ctx.with_filler("Still checking seats on the later flights.", delay=4):
                await asyncio.sleep(SEARCH_SECONDS)
        finally:
            if hold:
                hold.stop()
        self.state["options"] = [dict(option) for option in OPTIONS]
        self.state["search"] = "done"
        self.state["booking"]["status"] = "options"
        self.log("tool", f"search_flights returned {len(OPTIONS)} options")
        return self._options_text()

    def _options_text(self) -> str:
        return "Options: " + "; ".join(
            f"{o['id']}: {o['flight']} departs {o['depart']}, arrives {o['arrive']}, "
            f"{o['stops']}, seat {o['seat']}"
            for o in OPTIONS
        )

    @function_tool()
    async def check_policy(
        self, ctx: RunContext, topic: Literal["seat", "bags", "meal", "hotel", "refund"]
    ) -> str:
        """Answer a question about seats, bags, meals, hotels or refunds. Instant."""
        running = " while the search keeps running" if self.state["search"] == "running" else ""
        self.log("tool", f"check_policy({topic}) answered{running}")
        return POLICY[topic]

    @function_tool()
    async def rebook(self, ctx: RunContext, option: Literal["A", "B", "C"]) -> str:
        """Rebook the caller onto option A, B or C after they choose it."""
        if not self.state["options"]:
            return "Search first; there are no options yet."
        chosen = next(o for o in self.state["options"] if o["id"] == option)
        self.state["rebooked"] = chosen
        self.state["booking"].update(status="rebooked", flight=chosen["flight"])
        self.state["booking"].update(depart=chosen["depart"], seat=chosen["seat"])
        self.log("tool", f"rebook({option}) confirmed {chosen['flight']}")
        return f"Rebooked on {chosen['flight']}, {chosen['depart']}, seat {chosen['seat']}."

    @function_tool()
    async def simulate_outage(self, ctx: RunContext, part: Literal["voice", "model"]) -> str:
        """Simulate a provider outage when the caller asks to knock out the voice or model."""
        key = "tts" if part == "voice" else "llm"
        if self.outage.down(key):
            return f"The {part} outage is already running."
        self.begin_outage(key, "Caller-triggered outage")
        return f"The primary {part} is now failing. Keep helping the caller."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="flight-rebooking")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    agent = FlightRebooker(ctx.room)
    primary_llm, primary_tts = build_llm(agent.outage), build_tts(agent.outage)
    agent.watch(primary_llm, primary_tts)
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=primary_llm,
        tts=primary_tts,
        vad=ctx.proc.userdata["vad"],
    )
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()
    await agent.start_background(session)
    agent.publish()
    await session.generate_reply(
        instructions="Say their 18:05 flight to Vancouver is cancelled and offer to rebook."
    )


if __name__ == "__main__":
    cli.run_app(server)
