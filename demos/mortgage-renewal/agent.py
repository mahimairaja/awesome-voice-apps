"""Mortgage renewal advisor: a voice agent that knows "mm-hm" from "wait, stop".

A fictional bank's advisor walks the caller through renewing a mortgage. The
caller is invited to nod along ("right", "mm-hm") and to cut in. A backchannel
gate classifies every stretch of speech that overlaps the agent:

- backchannel: only acknowledgement words, so the agent keeps talking;
- barge-in: any real word, so the agent stops at once and answers;
- noise: speech-like audio with no words (a cough, a door), ignored.

Voice activity alone never pauses the agent here. The session's ``min_words``
floor is set high so the framework hands the decision to the gate, which reads
the interim transcript and calls ``session.interrupt()`` itself. When the agent
is cut off, LiveKit keeps only the words the caller actually heard in the chat
history; the panel shows that cut next to the words that were never played.

Run it:
1. Copy .env.example to .env and fill the keys.
2. uv sync
3. uv run python agent.py console (or dev, with a LiveKit client)
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
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
from livekit.agents.llm import StopResponse
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger(__name__)

# Words that acknowledge without asking for the floor. "um" and "uh" are
# hesitations, not content, so they never count as a barge-in on their own.
BACKCHANNEL_WORDS = frozenset(
    {
        "mm",
        "mmm",
        "mhm",
        "mmhm",
        "mmhmm",
        "hm",
        "hmm",
        "uh",
        "um",
        "huh",
        "uhhuh",
        "ah",
        "oh",
        "ok",
        "okay",
        "kay",
        "right",
        "yeah",
        "yep",
        "yup",
        "yes",
        "sure",
        "cool",
        "alright",
        "gotcha",
        "nice",
        "great",
        "true",
        "totally",
        "exactly",
    }
)
# Short phrases that are acknowledgements as a whole ("got it", "I see").
BACKCHANNEL_PHRASES = (
    ("makes", "sense"),
    ("got", "it"),
    ("i", "see"),
    ("for", "sure"),
    ("all", "right"),
    ("that", "makes", "sense"),
)

Kind = Literal["barge-in", "backchannel", "noise"]

# Seconds to wait after the caller stops before scoring the overlap, so the
# final transcript can land first.
SETTLE_SECONDS = 0.8
# How many overlaps the panel keeps.
KEEP = 6
# The session-level floor that stops raw voice activity from pausing the agent.
# Anything shorter is the gate's call; nobody says this many words as a nod.
GATE_MIN_WORDS = 50
TURN_HANDLING = {
    "turn_detection": "vad",
    "interruption": {"mode": "vad", "min_words": GATE_MIN_WORDS},
}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower().replace("-", ""))


def classify(text: str) -> Kind:
    """Score one overlap from its transcript."""
    tokens = words(text)
    if not tokens:
        return "noise"
    i = 0
    while i < len(tokens):
        for phrase in BACKCHANNEL_PHRASES:
            if tuple(tokens[i : i + len(phrase)]) == phrase:
                i += len(phrase)
                break
        else:
            if tokens[i] not in BACKCHANNEL_WORDS:
                return "barge-in"
            i += 1
    return "backchannel"


def split_heard(heard: str, planned: str) -> str:
    """Return the planned words the caller never heard."""
    count = len(heard.split())
    return " ".join(planned.split()[count:])


class OverlapGate:
    """Tracks speech that overlaps the agent and decides what it was.

    Pure bookkeeping driven by session events, so it can be tested without a
    room. ``interrupt`` stops the agent; ``publish`` receives the panel state;
    ``later`` schedules a callback after a delay.
    """

    def __init__(
        self,
        interrupt: Callable[[], None],
        publish: Callable[[dict], None],
        later: Callable[[float, Callable[[], None]], object],
    ) -> None:
        self.interrupt = interrupt
        self.publish = publish
        self.later = later
        self.agent_speaking = False
        self.current: dict | None = None
        self.records: list[dict] = []
        self.counts: dict[str, int] = {"barge-in": 0, "backchannel": 0, "noise": 0}
        self.last_backchannel_at = 0.0

    # Session events -------------------------------------------------------

    def on_agent_state(self, state: str, at: float) -> None:
        if state == "speaking":
            self.agent_speaking = True
            return
        if self.agent_speaking and self.current and self.current["interrupted"]:
            self.current.setdefault("stopped_at", at)
        self.agent_speaking = False

    def on_user_state(self, state: str, at: float) -> None:
        if state == "speaking":
            if self.current is None and self.agent_speaking:
                self._open(at)
            elif self.current is not None:
                self.current["settle"] += 1
            return
        if self.current is not None:
            current, generation = self.current, self.current["settle"]

            def settle() -> None:
                if self.current is current and current["settle"] == generation:
                    self._close()

            self.later(SETTLE_SECONDS, settle)

    def on_transcript(self, text: str, final: bool, at: float) -> None:
        if not text.strip():
            return
        if self.current is None:
            if not self.agent_speaking:
                return
            self._open(at)
        current = self.current
        current["said"] = " ".join(filter(None, [current["final"], text])).strip()
        if final:
            current["final"] = current["said"]
        if classify(current["said"]) == "barge-in" and not current["interrupted"]:
            current["interrupted"] = True
            current["decided_at"] = at
            if self.agent_speaking:
                self.interrupt()

    def on_cut(self, heard: str, planned: str) -> None:
        """The agent was cut off; record the words that reached the caller."""
        target = self.current
        if target is None or not target["interrupted"]:
            target = next(
                (r for r in reversed(self.records) if r["kind"] == "barge-in" and r["cut"] is None),
                None,
            )
        if target is None:
            return
        heard = heard.strip()
        cut = {"heard": heard[-160:], "unheard": split_heard(heard, planned)[:160]}
        if target is self.current:
            target["pending_cut"] = cut
        else:
            target["cut"] = cut
            self._publish()

    def ignore_turn(self, text: str, now: float) -> bool:
        """True when a finished turn was only a nod made over the agent."""
        if classify(text) == "barge-in":
            return False
        if self.current is not None and not self.current["interrupted"]:
            return True
        return now - self.last_backchannel_at < 4

    # Internals ------------------------------------------------------------

    def _open(self, at: float) -> None:
        self.current = {
            "start": at,
            "said": "",
            "final": "",
            "interrupted": False,
            "settle": 0,
        }

    def _close(self) -> None:
        current, self.current = self.current, None
        if current is None:
            return
        if current["interrupted"]:
            kind: Kind = "barge-in"
        else:
            kind = classify(current["said"])
            if kind == "backchannel":
                self.last_backchannel_at = time.time()
        record = {
            "kind": kind,
            "said": current["said"][:80],
            "decide_ms": _ms(current, "decided_at"),
            "stop_ms": _ms(current, "stopped_at"),
            "cut": current.get("pending_cut"),
        }
        self.counts[kind] += 1
        self.records = [*self.records, record][-KEEP:]
        self._publish()

    def _publish(self) -> None:
        self.publish({"overlaps": [dict(r) for r in self.records], "counts": dict(self.counts)})


def _ms(current: dict, key: str) -> int | None:
    if key not in current:
        return None
    return max(0, round((current[key] - current["start"]) * 1000))


# The renewal ----------------------------------------------------------------

BANK = "Northbeam Bank"
BALANCE = 401_160
REMAINING_YEARS = 20
CURRENT = {"term": "5-year fixed", "rate": 1.89, "payment": 2007}
# Illustrative rates for the simulation, not a quote.
OPTIONS = {
    "5-year fixed": 4.19,
    "3-year fixed": 3.99,
    "5-year variable": 4.45,
}


def monthly_payment(rate: float, balance: int = BALANCE, years: int = REMAINING_YEARS) -> int:
    """Canadian fixed-rate convention: interest compounds semi-annually."""
    monthly = (1 + rate / 100 / 2) ** (1 / 6) - 1
    return round(balance * monthly / (1 - (1 + monthly) ** (-years * 12)))


def renewal_props(selected: str | None) -> dict:
    return {
        "bank": BANK,
        "balance": f"${BALANCE:,}",
        "current": f"{CURRENT['term']} at {CURRENT['rate']:.2f}%, ${CURRENT['payment']:,}/mo",
        "options": [
            {
                "term": term,
                "rate": f"{rate:.2f}%",
                "payment": f"${monthly_payment(rate):,}",
            }
            for term, rate in OPTIONS.items()
        ],
        "selected": selected,
    }


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
) -> None:
    envelope = {"type": "ui_event", "component": component, "action": action, "props": props or {}}
    try:
        payload = json.dumps(envelope).encode("utf-8")
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except (TypeError, ValueError, RuntimeError):
        logger.exception("failed to publish playground ui event")
        return

    def log_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception():
            logger.warning("failed to publish playground ui event")

    task.add_done_callback(log_failure)


INSTRUCTIONS = f"""You are a mortgage renewal advisor at {BANK}, a fictional bank, on a voice call.
This is a simulation with a made-up mortgage; never give real financial advice.
The caller's mortgage renews next month. Balance ${BALANCE:,}, {REMAINING_YEARS} years left.
They are on a {CURRENT["term"]} at {CURRENT["rate"]}%, paying ${CURRENT["payment"]:,} a month.
Renewal options (illustrative rates): {", ".join(f"{t} at {r}%" for t, r in OPTIONS.items())}.

Explain in spoken paragraphs of three or four sentences, so the caller has room to
nod along or cut in. Cover fixed versus variable, the payment change, and what
happens if rates move. When you name an option's payment, call compare_option so
it appears on screen. Plain text only, no lists, no markdown, numbers spoken
naturally.

If your previous message in the history ends abruptly, the caller cut you off
there and heard nothing after it. Answer what they said first. Do not repeat
what they already heard; offer to pick up from where you stopped."""


class MortgageAdvisor(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self.gate: OverlapGate | None = None
        self._planned: list[str] = []

    def watch(self, session: AgentSession) -> None:
        """Wire the backchannel gate to this call's session events."""
        loop = asyncio.get_running_loop()

        def interrupt() -> None:
            try:
                session.interrupt()
            except RuntimeError:
                logger.debug("current speech cannot be interrupted")

        self.gate = gate = OverlapGate(
            interrupt=interrupt,
            publish=lambda props: publish_ui_event(self.room, "Overlaps", "update", props),
            later=loop.call_later,
        )
        session.on("agent_state_changed", lambda e: gate.on_agent_state(e.new_state, e.created_at))
        session.on("user_state_changed", lambda e: gate.on_user_state(e.new_state, e.created_at))
        session.on(
            "user_input_transcribed",
            lambda e: gate.on_transcript(e.transcript, e.is_final, e.created_at),
        )

        def item_added(event) -> None:
            item = event.item
            if getattr(item, "role", None) == "assistant" and getattr(item, "interrupted", False):
                gate.on_cut(item.text_content or "", "".join(self._planned))

        session.on("conversation_item_added", item_added)

    def publish_initial(self) -> None:
        publish_ui_event(self.room, "Renewal", "mount", renewal_props(None))
        publish_ui_event(
            self.room,
            "Overlaps",
            "mount",
            {"overlaps": [], "counts": {"barge-in": 0, "backchannel": 0, "noise": 0}},
        )

    async def tts_node(self, text, model_settings):
        # Keep what the agent meant to say, to show what an interruption cut off.
        planned: list[str] = []
        self._planned = planned

        async def tee():
            async for chunk in text:
                planned.append(chunk)
                yield chunk

        async for frame in super().tts_node(tee(), model_settings):
            yield frame

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        # A nod that outlived the agent's sentence is still a nod: no reply.
        if self.gate and self.gate.ignore_turn(new_message.text_content or "", time.time()):
            raise StopResponse()

    @function_tool()
    async def compare_option(
        self,
        context: RunContext,
        term: Literal["5-year fixed", "3-year fixed", "5-year variable"],
    ) -> str:
        """Show one renewal option's monthly payment on the caller's screen."""
        publish_ui_event(self.room, "Renewal", "update", renewal_props(term))
        payment = monthly_payment(OPTIONS[term])
        change = payment - CURRENT["payment"]
        return f"{term} at {OPTIONS[term]}%: ${payment:,} a month, ${change:,} more than today."


GREETING = (
    f"Say you are the renewal advisor at {BANK}, that this is a simulation with a made-up "
    "mortgage, and that they can say mm-hm as you go or cut in any time. Then start "
    "explaining that their five-year fixed renews next month and what has changed since 2021."
)

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="mortgage-renewal")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-2"),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TURN_HANDLING,
    )
    agent = MortgageAdvisor(ctx.room)
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()
    agent.watch(session)
    agent.publish_initial()
    await session.generate_reply(instructions=GREETING)


if __name__ == "__main__":
    cli.run_app(server)
