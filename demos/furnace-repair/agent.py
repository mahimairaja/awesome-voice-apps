"""furnace-repair: an emergency heating line that lets callers finish their sentence.

It is -20 outside and the furnace is dead. The caller gives an address, a
callback number and the problem, pausing the way people do ("it's 42... uh...
Maple"). LiveKit's audio turn detector decides when they are done. A plain
silence timer runs in shadow on the same audio, and the screen shows every
place it would have cut the caller off.

Stack: Deepgram STT, OpenAI LLM, Cartesia TTS, LiveKit audio turn detector.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Literal

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
from turns import TurnTimeline, WatchedTurnDetector

load_dotenv()

logger = logging.getLogger(__name__)

COMPANY = "Brightline Heating"
TICKET_FIELDS = ("problem", "address", "callback", "safety")
LABELS = {
    "problem": "problem",
    "address": "address",
    "callback": "callback",
    "safety": "safety",
    "priority": "priority",
}
_STREET = re.compile(r"^\s*\d+[A-Za-z]?\s+\S+")


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
        logger.exception("failed to encode ui event")
        return
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except RuntimeError:
        logger.exception("failed to schedule ui event")
        return

    def log_failure(done: asyncio.Task[None]) -> None:
        if not done.cancelled() and done.exception():
            logger.warning("failed to publish ui event", exc_info=done.exception())

    task.add_done_callback(log_failure)


def _ui_action(room: rtc.Room, component_id: str) -> Literal["mount", "update"]:
    mounted = getattr(room, "_awesome_voice_ui_mounted", None)
    if mounted is None:
        mounted = set()
        setattr(room, "_awesome_voice_ui_mounted", mounted)
    if component_id in mounted:
        return "update"
    mounted.add(component_id)
    return "mount"


# Validation --------------------------------------------------------------


def normalize_phone(value: str) -> tuple[bool, str]:
    """A North American callback number, as 10 digits."""
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return False, f"heard {len(digits)} digits; a callback number needs 10"
    if digits[0] in "01" or digits[3] in "01":
        return False, "that is not a valid North American number"
    return True, f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def spoken_digits(phone: str) -> str:
    """How to read a number back: grouped digits, never as a quantity."""
    digits = re.sub(r"\D", "", phone)
    return ", ".join(" ".join(group) for group in (digits[:3], digits[3:6], digits[6:]))


def normalize_address(value: str) -> tuple[bool, str]:
    address = " ".join(value.split()).strip(" ,.")
    if not _STREET.match(address):
        return False, "need a house number and a street name"
    if len(address) > 80:
        return False, "too long; ask for just the street address and town"
    return True, address


def priority(ticket: dict) -> str:
    if ticket.get("gas"):
        return "gas: leave the home"
    if ticket.get("vulnerable"):
        return "emergency, vulnerable occupant"
    return "emergency, no heat"


def new_ticket() -> dict:
    return {"ticket": {}, "ref": None}


def ticket_rows(ticket: dict) -> list[dict]:
    rows = [{"label": LABELS[f], "value": ticket.get(f) or "-"} for f in TICKET_FIELDS]
    rows.append({"label": "priority", "value": priority(ticket) if "safety" in ticket else "-"})
    return rows


def publish_ticket(room: rtc.Room, data: dict) -> None:
    rows = ticket_rows(data["ticket"])
    if data.get("ref"):
        rows.append({"label": "ticket", "value": data["ref"]["id"]})
    publish_ui_event(
        room,
        "KeyValue",
        _ui_action(room, "ticket"),
        component_id="ticket",
        props={"title": "service call", "items": rows},
    )


def _ticket_ref() -> str:
    return f"BHL-{uuid.uuid4().hex[:5].upper()}"


INSTRUCTIONS = (
    f"You answer the after-hours emergency line for {COMPANY}, a furnace repair company. "
    "It is about minus 20 outside, so a dead furnace is urgent. Collect four things: "
    "what is wrong with the heat, the street address, a callback number, and two "
    "safety answers (does anyone smell gas, and is anyone at home elderly, an infant "
    "or unwell). Record answers with update_ticket as soon as you hear them; the "
    "caller may give several at once. Callers pause mid-sentence while they think "
    "or read a number: wait for the whole answer and never guess the missing part. "
    "When update_ticket returns a read-back, read the address and the phone digits "
    "back exactly as given and ask if they are right. If anyone smells gas, tell "
    "them to leave the home now and call their gas utility's emergency line or 911 "
    "from outside, and do not book a visit. Otherwise, once everything is recorded "
    "and confirmed, call dispatch_technician and tell them the ticket number and the "
    "arrival window. One short question at a time. Plain text, no markdown or emojis."
)


class FurnaceLine(Agent):
    # Passed to LiveKit's TurnDetector. Empty picks the version for the environment.
    detector_options: dict[str, Any] = {}

    def __init__(self, room: rtc.Room) -> None:
        self.room = room
        self.timeline = TurnTimeline(self._publish_timeline)
        detector = WatchedTurnDetector(
            on_prediction=self.timeline.prediction,
            on_model=self.timeline.model_changed,
            **self.detector_options,
        )
        self.timeline.model = detector.model
        super().__init__(instructions=INSTRUCTIONS, turn_handling={"turn_detection": detector})

    # The turn timeline ----------------------------------------------------

    def watch_turns(self, session: AgentSession, vad: Any = None) -> None:
        """Feed session events to the shadow timeline. Call once per call."""
        silence = getattr(getattr(vad, "_opts", None), "min_silence_duration", None)
        if isinstance(silence, int | float) and silence > 0:
            self.timeline.timer_silence = float(silence)
        session.on("user_state_changed", lambda ev: self.timeline.user_state(ev.new_state))
        session.on(
            "user_input_transcribed",
            lambda ev: self.timeline.transcript(ev.transcript, ev.is_final),
        )
        self._publish_timeline(self.timeline.snapshot())

    def _publish_timeline(self, props: dict) -> None:
        props = {**props, "timer_ms": round(self.timeline.timer_silence * 1000)}
        publish_ui_event(self.room, "TurnTimeline", _ui_action(self.room, "turns"), props, "turns")

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        self.timeline.commit(new_message.text_content or "")

    # Tools ----------------------------------------------------------------

    @function_tool()
    async def update_ticket(
        self,
        context: RunContext[dict],
        problem: str | None = None,
        address: str | None = None,
        callback_number: str | None = None,
        gas_smell: bool | None = None,
        vulnerable_occupant: bool | None = None,
    ) -> str:
        """Record what the caller said on the service ticket. Pass only what they gave.

        Args:
            problem: What is wrong with the heat, in a few words.
            address: Street address with house number, and town if given.
            callback_number: The phone number exactly as spoken.
            gas_smell: True if anyone smells gas or rotten eggs.
            vulnerable_occupant: True if someone elderly, an infant or unwell is home.
        """
        ticket = context.userdata["ticket"]
        notes: list[str] = []
        if problem:
            ticket["problem"] = " ".join(problem.split())[:80]
            notes.append("recorded the problem")
        if address:
            ok, value = normalize_address(address)
            if ok:
                ticket["address"] = value
                notes.append(f"address: read back '{value}' and ask if it is right")
            else:
                notes.append(f"address rejected: {value}")
        if callback_number:
            ok, value = normalize_phone(callback_number)
            if ok:
                ticket["callback"] = value
                notes.append(f"callback: read back the digits {spoken_digits(value)}")
            else:
                notes.append(f"callback rejected: {value}; ask them to repeat it")
        if gas_smell is not None:
            ticket["gas"] = gas_smell
        if vulnerable_occupant is not None:
            ticket["vulnerable"] = vulnerable_occupant
        if "gas" in ticket and "vulnerable" in ticket:
            parts = ["gas smell" if ticket["gas"] else "no gas"]
            parts.append("vulnerable occupant" if ticket["vulnerable"] else "no one at risk")
            ticket["safety"] = ", ".join(parts)
        if ticket.get("gas"):
            notes.append("GAS: tell them to leave now and call the gas utility or 911 outside")
        publish_ticket(self.room, context.userdata)
        missing = [LABELS[f] for f in TICKET_FIELDS if not ticket.get(f)]
        notes.append(f"still needed: {', '.join(missing)}" if missing else "ticket complete")
        return "; ".join(notes) or "nothing recorded"

    @function_tool()
    async def dispatch_technician(self, context: RunContext[dict]) -> str:
        """Book the emergency visit once the caller has confirmed the ticket."""
        data = context.userdata
        ticket = data["ticket"]
        missing = [LABELS[f] for f in TICKET_FIELDS if not ticket.get(f)]
        if missing:
            return f"cannot dispatch yet, still missing: {', '.join(missing)}"
        if ticket.get("gas"):
            return "do not dispatch: the caller must leave and call the gas utility first"
        if not data.get("ref"):
            window = "within 2 hours" if ticket.get("vulnerable") else "within 4 hours"
            data["ref"] = {"id": _ticket_ref(), "window": window}
        publish_ticket(self.room, data)
        ref = data["ref"]
        publish_ui_event(
            self.room,
            "Card",
            _ui_action(self.room, "dispatched"),
            component_id="dispatched",
            props={
                "title": "technician dispatched",
                "body": f"{ticket['address']} · {ref['window']}",
                "footer": ref["id"],
                "accent": True,
            },
        )
        return f"Dispatched. Ticket {ref['id']}, technician arrives {ref['window']}."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="furnace-repair")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    vad = ctx.proc.userdata["vad"]
    session = AgentSession(
        userdata=new_ticket(),
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-2"),
        vad=vad,
    )
    agent = FurnaceLine(ctx.room)
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()
    agent.watch_turns(session, vad)
    publish_ticket(ctx.room, session.userdata)
    await session.generate_reply(
        instructions=f"Answer as the {COMPANY} emergency line and ask what is wrong with the heat."
    )


if __name__ == "__main__":
    cli.run_app(server)
