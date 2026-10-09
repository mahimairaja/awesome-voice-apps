"""Billing dispute line: a voice agent that reads the room and knows when to hand off.

A caller disputes a $215 phone bill from Tellwave Mobile, a fictional carrier.
The bill has three charges they never expected. Beside the LLM, a frustration
meter scores every caller turn from plain, explainable signals:

- words: anger ("ridiculous"), profanity, threats ("I'm cancelling"), asking
  for a person, and repetition ("I already told you");
- timing: talking over the agent, speaking fast, and long rants;
- relief: thanks, "okay, that helps", and a credit landing on the bill.

The score picks one de-escalation move for the next reply (reflect back, name
the feeling, offer a choice, take ownership...) and the Cartesia sonic-3 voice
settings to say it with: slower and softer as the call heats up. When the
score crosses the handoff line, stays high for three turns, or the caller asks
for a person twice, the code (not the model) starts a warm transfer and hands
over a case summary so the caller never repeats themselves.

Run it:
1. Copy .env.example to .env and fill the keys.
2. uv sync
3. uv run python agent.py console (or dev, with a LiveKit client)
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
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
    utils,
)
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger(__name__)

# The bill --------------------------------------------------------------------

COMPANY = "Tellwave Mobile"
ACCOUNT = "TW-48213"
VOICE = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
# The most the agent may credit on its own. More needs a billing specialist.
AUTHORITY = 75.0

LineId = Literal["plan", "roaming", "protection", "late_fee"]


@dataclass
class Line:
    id: str
    label: str
    amount: float
    # What policy lets the agent credit, and why. Zero means not disputable.
    credit_max: float
    facts: str
    credited: float = 0.0


def new_bill() -> dict[str, Line]:
    return {
        "plan": Line(
            "plan",
            "Unlimited 40 plan",
            65.00,
            0.0,
            "The usual monthly plan price, unchanged since they joined.",
        ),
        "roaming": Line(
            "roaming",
            "US roaming, 9 days",
            108.00,
            63.00,
            "Pay-per-day roaming at $12 a day during a trip to Seattle. A Travel Pass would "
            "have been $5 a day, $45 in total. Policy allows re-rating to the Travel Pass "
            "price once, a $63 credit.",
        ),
        "protection": Line(
            "protection",
            "Device protection",
            17.00,
            17.00,
            "Added on the 3rd during a store visit. No signed consent on file, so it can be "
            "removed and credited in full.",
        ),
        "late_fee": Line(
            "late_fee",
            "Late payment fee",
            25.00,
            25.00,
            "Autopay failed because the card on file expired. First late fee on the account, "
            "so it can be waived once.",
        ),
    }


def bill_props(bill: dict[str, Line], focus: str | None = None) -> dict:
    credited = round(sum(line.credited for line in bill.values()), 2)
    total = round(sum(line.amount for line in bill.values()), 2)
    return {
        "company": COMPANY,
        "account": ACCOUNT,
        "lines": [
            {
                "id": line.id,
                "label": line.label,
                "amount": line.amount,
                "credited": line.credited,
                "disputed": line.credit_max > 0,
            }
            for line in bill.values()
        ],
        "total": total,
        "credited": credited,
        "due": round(total - credited, 2),
        "authority_left": round(AUTHORITY - credited, 2),
        "focus": focus,
    }


# The frustration meter -------------------------------------------------------

Band = Literal["calm", "tense", "heated", "handoff"]
Strategy = Literal[
    "explain",
    "acknowledge",
    "label",
    "reflect",
    "choice",
    "own",
    "confirm",
    "handoff",
]

START = 40
HANDOFF_AT = 85
# One outburst moves the meter a lot, but never straight to a transfer.
MAX_RISE = 25
# Three turns in a row at or above this also hand off: anger that will not cool.
SUSTAINED_AT = 70
SUSTAINED_TURNS = 3
# A caller who hears nothing new still cools a little each turn they are heard.
DECAY = 5
RELIEF = 15
FAST_WPS = 3.3
RANT_WORDS = 30
KEEP = 8

# Each cue is matched on word boundaries in the lowercased transcript.
CUES: dict[str, tuple[int, tuple[str, ...]]] = {
    "anger": (
        12,
        (
            "ridiculous",
            "unacceptable",
            "absurd",
            "insane",
            "joke",
            "scam",
            "rip off",
            "ripoff",
            "robbery",
            "furious",
            "outrageous",
            "fed up",
            "sick of",
            "pissed",
            "angry",
            "frustrated",
            "fraud",
            "stealing",
            "garbage",
            "terrible",
        ),
    ),
    "profanity": (15, ("damn", "hell", "crap", "shit", "fuck", "fucking", "bs", "bullshit")),
    "threat": (
        18,
        (
            "cancel",
            "cancelling",
            "canceling",
            "switching",
            "leaving",
            "lawyer",
            "complaint",
            "report you",
            "crtc",
            "ccts",
            "better business bureau",
            "twitter",
        ),
    ),
    "human": (
        10,
        (
            "manager",
            "supervisor",
            "human",
            "real person",
            "someone else",
            "transfer me",
            "representative",
            "agent",
        ),
    ),
    "repeat": (
        10,
        (
            "already told you",
            "i told you",
            "how many times",
            "third time",
            "second time",
            "keep saying",
            "you said that",
            "not listening",
            "listen to me",
        ),
    ),
    "calm": (
        -12,
        (
            "thank you",
            "thanks",
            "that helps",
            "appreciate",
            "fair enough",
            "that's fair",
            "okay good",
            "great",
            "perfect",
            "sounds good",
            "makes sense",
        ),
    ),
}
LABELS = {
    "anger": "Anger word",
    "profanity": "Profanity",
    "threat": "Threat to leave",
    "human": "Asked for a person",
    "repeat": "Repeating themselves",
    "barge_in": "Talked over the agent",
    "fast": "Speaking fast",
    "rant": "Long rant",
    "calm": "Relief",
    "progress": "Credit applied",
}
WEIGHTS = {"barge_in": 10, "fast": 8, "rant": 6, "progress": -RELIEF}

# How each band sounds. sonic-3 speed runs 0.6 to 1.5; emotion is a beta hint.
VOICE_BY_BAND: dict[str, dict] = {
    "calm": {"speed": 1.0, "emotion": "content"},
    "tense": {"speed": 0.95, "emotion": "calm"},
    "heated": {"speed": 0.88, "emotion": "sympathetic"},
    "handoff": {"speed": 0.9, "emotion": "calm"},
}

STRATEGIES: dict[str, tuple[str, str]] = {
    "explain": (
        "Explain",
        "Explain plainly, one charge at a time, and check it landed.",
    ),
    "acknowledge": (
        "Acknowledge first",
        "Open with one short acknowledgement of how this bill feels, then the facts.",
    ),
    "label": (
        "Name the feeling",
        (
            "Name the emotion you hear in one sentence (for example 'that sounds like it "
            "blindsided you'), then apologise for the exact charge, not in general."
        ),
    ),
    "reflect": (
        "Reflect back",
        (
            "They feel unheard. Start by repeating back what they told you in their own words, "
            "in one sentence. Do not re-explain anything you already said."
        ),
    ),
    "choice": (
        "Offer a choice",
        (
            "Give them control: offer exactly two options, you fix the charges now, or you "
            "bring in a billing specialist. Let them pick."
        ),
    ),
    "own": (
        "Take ownership",
        (
            "Take ownership: say what you will do, the exact amount, and that it shows on this "
            "bill. Then do it with apply_credit. No policy language."
        ),
    ),
    "confirm": (
        "Confirm the next step",
        "They are cooling down. Confirm what is done and the one next step. Keep it short.",
    ),
    "handoff": (
        "Warm handoff",
        (
            "Hand off warmly: say you are bringing in a billing specialist who can approve more "
            "than you can, that you have passed on everything so they will not repeat "
            "themselves, and sum up the case in one sentence. Then stop. Do not call tools."
        ),
    ),
}


def find_cues(text: str) -> dict[str, list[str]]:
    lowered = " " + re.sub(r"[^a-z' ]+", " ", text.lower()) + " "
    found: dict[str, list[str]] = {}
    for kind, (_, cues) in CUES.items():
        hits = [cue for cue in cues if f" {cue} " in lowered]
        if hits:
            found[kind] = hits
    return found


def band_for(score: int) -> Band:
    if score >= HANDOFF_AT:
        return "handoff"
    if score >= 60:
        return "heated"
    if score >= 35:
        return "tense"
    return "calm"


def pick_strategy(band: Band, kinds: set[str]) -> Strategy:
    """One move per reply, most specific signal first."""
    if band == "handoff":
        return "handoff"
    if "repeat" in kinds:
        return "reflect"
    if "human" in kinds:
        return "choice"
    if "threat" in kinds:
        return "own"
    if kinds & {"anger", "profanity"}:
        return "label"
    if kinds & {"calm", "progress"}:
        return "confirm"
    return "explain" if band == "calm" else "acknowledge"


@dataclass
class FrustrationMeter:
    """Scores caller turns. Pure bookkeeping, so it is tested without a room."""

    score: int = START
    turn: int = 0
    peak: int = START
    asked_for_human: int = 0
    high_streak: int = 0
    pending_relief: int = 0
    handoff_reason: str | None = None
    points: list[int] = field(default_factory=lambda: [START])
    turns: list[dict] = field(default_factory=list)

    def relief(self) -> None:
        """A credit landed; the next turn starts from a better place."""
        self.pending_relief += 1

    def score_turn(self, text: str, seconds: float, barged_in: bool) -> dict:
        self.turn += 1
        words = len(text.split())
        found = find_cues(text)
        signals: list[dict] = []
        for kind, hits in found.items():
            weight = CUES[kind][0]
            # Two hits of one kind count; a long rant should not saturate the meter.
            for cue in hits[:2]:
                signals.append({"kind": kind, "cue": cue, "weight": weight})
        if barged_in:
            signals.append({"kind": "barge_in", "cue": "", "weight": WEIGHTS["barge_in"]})
        if seconds > 0.8 and words >= 6 and words / seconds > FAST_WPS:
            signals.append(
                {"kind": "fast", "cue": f"{words / seconds:.1f} w/s", "weight": WEIGHTS["fast"]}
            )
        if words >= RANT_WORDS:
            signals.append({"kind": "rant", "cue": f"{words} words", "weight": WEIGHTS["rant"]})
        for _ in range(min(self.pending_relief, 2)):
            signals.append({"kind": "progress", "cue": "", "weight": WEIGHTS["progress"]})
        self.pending_relief = 0

        delta = min(MAX_RISE, sum(s["weight"] for s in signals) - DECAY)
        previous = self.score
        self.score = max(0, min(100, self.score + delta))
        self.peak = max(self.peak, self.score)
        self.points = [*self.points, self.score][-(KEEP + 1) :]
        if "human" in found:
            self.asked_for_human += 1
        self.high_streak = self.high_streak + 1 if self.score >= SUSTAINED_AT else 0

        band = band_for(self.score)
        if not self.handoff_reason:
            if band == "handoff":
                self.handoff_reason = f"Frustration reached {self.score}"
            elif self.high_streak >= SUSTAINED_TURNS:
                self.handoff_reason = f"Above {SUSTAINED_AT} for {SUSTAINED_TURNS} turns"
            elif self.asked_for_human >= 2:
                self.handoff_reason = "Asked for a person twice"
        if self.handoff_reason:
            band = "handoff"
        strategy = pick_strategy(band, {s["kind"] for s in signals})
        record = {
            "turn": self.turn,
            "said": text.strip()[:80],
            "score": self.score,
            "delta": self.score - previous,
            "signals": [{"kind": s["kind"], "cue": s["cue"][:24]} for s in signals][:6],
            "band": band,
            "strategy": strategy,
            "voice": dict(VOICE_BY_BAND[band]),
        }
        self.turns = [*self.turns, record][-KEEP:]
        return record

    def props(self) -> dict:
        band = "handoff" if self.handoff_reason else band_for(self.score)
        return {
            "score": self.score,
            "peak": self.peak,
            "band": band,
            "threshold": HANDOFF_AT,
            "points": list(self.points),
            "turns": [dict(t) for t in self.turns],
            "voice": dict(VOICE_BY_BAND[band]),
        }


def strategy_instruction(record: dict) -> str:
    title, move = STRATEGIES[record["strategy"]]
    cues = ", ".join(LABELS[s["kind"]].lower() for s in record["signals"]) or "none"
    brevity = {
        "calm": "Up to three sentences.",
        "tense": "Two or three short sentences.",
        "heated": "Two short sentences at most, under fifteen words each.",
        "handoff": "Three short sentences at most.",
    }[record["band"]]
    return (
        f"Caller frustration is {record['score']} out of 100 ({record['band']}). "
        f"Signals this turn: {cues}. Your move for this reply: {title}. {move} {brevity}"
    )


# The agent -------------------------------------------------------------------


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


INSTRUCTIONS = f"""You are a billing support agent at {COMPANY}, a fictional phone carrier, on a voice call.
This is a simulation with a made-up account ({ACCOUNT}); nothing real is charged or credited.
The caller is disputing this month's bill of $215.00. Their plan is usually $65.

The bill:
- Unlimited 40 plan, $65.00. Correct, not disputable.
- US roaming, 9 days, $108.00. Pay-per-day at $12 a day in Seattle. A Travel Pass would have been $45. You may re-rate it once: a $63 credit.
- Device protection, $17.00. Added in store on the 3rd with no signed consent. You may remove it and credit $17.
- Late payment fee, $25.00. Autopay failed because their card expired. First late fee, so you may waive it: $25.

You may credit up to ${AUTHORITY:.0f} in total on your own. The three credits add up to $105, so
you cannot do all three. If the caller wants more than your limit, offer to bring in a billing
specialist with transfer_to_specialist. Call explain_charge when you talk about a line, and
apply_credit only after the caller agrees to that credit.

Each caller turn comes with a short note telling you their frustration score and the one
de-escalation move to use. Follow it. Never say "calm down", "per our policy", "unfortunately",
"I understand your frustration" or "as I said". Never argue about what they said. Plain spoken
text only: no lists, no markdown, amounts spoken naturally."""

GREETING = (
    f"Say you are with {COMPANY} billing, that this is a simulation with a made-up bill, and "
    "that they should play an upset customer: the bill on screen is $215 instead of $65. "
    "Then ask, in one short sentence, what is going on with their bill."
)


class BillingDesk(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self.bill = new_bill()
        self.meter = FrustrationMeter()
        self.handed_off = False
        self._agent_speaking = False
        self._barged_in = False
        self._started_at: float | None = None
        self._spoke_for = 0.0

    # Wiring -------------------------------------------------------------

    def watch(self, session: AgentSession) -> None:
        """Time each caller turn and notice when they talk over the agent."""

        def agent_state(event) -> None:
            self._agent_speaking = event.new_state == "speaking"

        def user_state(event) -> None:
            if event.new_state == "speaking":
                if self._started_at is None:
                    self._started_at = event.created_at
                if self._agent_speaking:
                    self._barged_in = True
            elif self._started_at is not None:
                self._spoke_for += max(0.0, event.created_at - self._started_at)
                self._started_at = None

        def item_added(event) -> None:
            # The handoff line has played; the transfer is the end of this call.
            item = event.item
            if self.handed_off and getattr(item, "role", None) == "assistant":
                asyncio.get_running_loop().call_later(1.0, self.end_call)

        session.on("agent_state_changed", agent_state)
        session.on("user_state_changed", user_state)
        session.on("conversation_item_added", item_added)

    def publish_initial(self) -> None:
        publish_ui_event(self.room, "Bill", "mount", bill_props(self.bill))
        publish_ui_event(self.room, "Mood", "mount", self.meter.props())

    def end_call(self) -> None:
        """Standalone runs end the session; the hosted worker ends the reservation."""
        self.session.shutdown()

    def _voice(self, settings: dict) -> None:
        tts = self.tts if utils.is_given(self.tts) and self.tts else self.session.tts
        if isinstance(tts, cartesia.TTS):
            tts.update_options(speed=settings["speed"], emotion=settings["emotion"])

    # Turns --------------------------------------------------------------

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        text = new_message.text_content or ""
        record = self.meter.score_turn(text, self._spoke_for, self._barged_in)
        self._barged_in = False
        self._spoke_for = 0.0
        self._voice(record["voice"])
        publish_ui_event(self.room, "Mood", "update", self.meter.props())
        if record["strategy"] == "handoff" and not self.handed_off:
            self.start_handoff(self.meter.handoff_reason or "Caller asked for a person")
        turn_ctx.add_message(role="system", content=strategy_instruction(record))

    def start_handoff(self, reason: str) -> None:
        self.handed_off = True
        open_items = [
            line.label for line in self.bill.values() if line.credit_max and not line.credited
        ]
        credited = sum(line.credited for line in self.bill.values())
        publish_ui_event(
            self.room,
            "Handoff",
            "mount",
            {
                "reason": reason[:80],
                "peak": self.meter.peak,
                "credited": round(credited, 2),
                "open": open_items,
                "summary": (
                    f"Disputed $215 bill on {ACCOUNT}. Credited ${credited:.2f}; "
                    f"still open: {', '.join(open_items) or 'nothing'}. "
                    f"Peak frustration {self.meter.peak}."
                ),
            },
        )

    # Tools --------------------------------------------------------------

    @function_tool()
    async def explain_charge(self, context: RunContext, line: LineId) -> str:
        """Show one bill line on the caller's screen and get the facts behind it."""
        item = self.bill[line]
        publish_ui_event(self.room, "Bill", "update", bill_props(self.bill, line))
        allowed = item.credit_max - item.credited
        return f"{item.label}, ${item.amount:.2f}. {item.facts} Creditable now: ${allowed:.2f}."

    @function_tool()
    async def apply_credit(self, context: RunContext, line: LineId) -> str:
        """Apply the policy credit for one bill line, after the caller agrees."""
        item = self.bill[line]
        if self.handed_off:
            return "The call is with the billing specialist now."
        if item.credit_max == 0:
            return f"{item.label} is correct and cannot be credited."
        if item.credited:
            return f"{item.label} is already credited ${item.credited:.2f}."
        used = sum(i.credited for i in self.bill.values())
        if used + item.credit_max > AUTHORITY:
            return (
                f"Over your limit: ${used:.2f} of ${AUTHORITY:.0f} used, this needs "
                f"${item.credit_max:.2f}. Offer the billing specialist."
            )
        item.credited = item.credit_max
        self.meter.relief()
        publish_ui_event(self.room, "Bill", "update", bill_props(self.bill, line))
        due = sum(i.amount - i.credited for i in self.bill.values())
        return f"Credited ${item.credited:.2f} for {item.label}. New balance ${due:.2f}."

    @function_tool()
    async def transfer_to_specialist(self, context: RunContext) -> str:
        """Bring in a billing specialist when the caller wants more than you can credit,
        or insists on a person."""
        if not self.handed_off:
            self.start_handoff("Needs more than the agent's credit limit")
        return STRATEGIES["handoff"][1]


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="billing-deescalation")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3", voice=VOICE, **VOICE_BY_BAND["tense"]),
        vad=ctx.proc.userdata["vad"],
    )
    agent = BillingDesk(ctx.room)
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()
    agent.watch(session)
    agent.publish_initial()
    await session.generate_reply(instructions=GREETING)


if __name__ == "__main__":
    cli.run_app(server)
