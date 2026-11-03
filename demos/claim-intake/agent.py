"""claim-intake: auto first-notice-of-loss (FNOL) intake by voice.

The caller reports a car accident; the agent collects the claim one field at a
time, validates each, fills a live form on screen, then reads the claim back and
files it with a reference number.

Stack: AssemblyAI Universal-3 Pro STT, Google Gemini 3 Flash LLM, Inworld TTS.

Run it:
1. cp .env.example .env and fill the keys (Inworld key is Base64-encoded).
2. uv sync
3. uv run python agent.py dev, then open
   https://playground.mahimai.ca/demos/claim-intake.
"""

import asyncio
import json
import logging
import re
import uuid
from datetime import date, timedelta
from typing import Callable, Literal

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
    inference,
)
from livekit.plugins import assemblyai, google, inworld

load_dotenv()

logger = logging.getLogger(__name__)

# Captured once at startup; the date of loss cannot be after this.
TODAY = date.today()

FieldName = Literal[
    "claimant_name",
    "policy_number",
    "date_of_loss",
    "location",
    "vehicle",
    "description",
    "injuries",
    "drivable",
]

FIELDS: list[str] = list(FieldName.__args__)

FIELD_LABELS: dict[str, str] = {
    "claimant_name": "name",
    "policy_number": "policy #",
    "date_of_loss": "date of loss",
    "location": "location",
    "vehicle": "vehicle",
    "description": "what happened",
    "injuries": "injuries",
    "drivable": "drivable",
}

POLICY_RE = re.compile(r"^[A-Z]{2}\d{6,8}$")
# Speech to text often writes a spelled policy number as words: "A B one two three".
DIGIT_WORDS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
MONTHS = (
    "january february march april may june july august september october november december"
).split()
_YES = {"yes", "y", "yeah", "yep", "yup", "true", "correct", "affirmative"}
_NO = {"no", "n", "nope", "nah", "false", "negative"}


def _nonempty(value: str) -> tuple[bool, str]:
    v = value.strip()
    return (True, v) if v else (False, "that came through empty; ask again")


def validate_policy_number(value: str) -> tuple[bool, str]:
    words = re.findall(r"[a-z0-9]+", value.lower())
    v = "".join(DIGIT_WORDS.get(w, w) for w in words).upper()
    if POLICY_RE.match(v):
        return True, v
    return False, (
        f"heard {v or 'nothing'}, but a policy number is two letters then six to eight "
        "digits, like AB123456"
    )


def parse_date(value: str, today: date) -> date | None:
    """Read "yesterday", "June 28, 2026", "28 June 2026", "2026-06-28" or "06/28/2026"."""
    text = re.sub(r"\b(\d{1,2})(st|nd|rd|th)\b", r"\1", value.strip().lower())
    text = " ".join(re.sub(r"\b(the|of|on|it|was|happened)\b|[,.]", " ", text).split())
    relative = {"today": 0, "yesterday": 1, "day before yesterday": 2}
    if text in relative:
        return today - timedelta(days=relative[text])
    if m := re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text):
        year, month, day = m.groups()
    elif m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):
        month, day, year = m.groups()
    elif m := re.fullmatch(r"([a-z]+) (\d{1,2})(?: (\d{4}))?", text):
        month, day, year = m.groups()
    elif m := re.fullmatch(r"(\d{1,2}) ([a-z]+)(?: (\d{4}))?", text):
        day, month, year = m.groups()
    else:
        return None
    if not month.isdigit():
        names = [name for name in MONTHS if name.startswith(month[:3])]
        if len(month) < 3 or not names:
            return None
        month = MONTHS.index(names[0]) + 1
    try:
        d = date(int(year or today.year), int(month), int(day))
    except ValueError:
        return None
    # A date given without a year is the most recent one.
    if year is None and d > today:
        d = d.replace(year=d.year - 1)
    return d


def validate_date_of_loss(value: str, today: date | None = None) -> tuple[bool, str]:
    today = today or TODAY
    d = parse_date(value, today)
    if d is None:
        return False, "that is not a date; ask which day it happened"
    if d > today:
        return False, "the date of loss cannot be in the future"
    return True, d.isoformat()


def normalize_yesno(value: str) -> tuple[bool, str]:
    v = value.strip().lower().rstrip(".!")
    if v in _YES:
        return True, "yes"
    if v in _NO:
        return True, "no"
    return False, "answer needs to be yes or no"


VALIDATORS: dict[str, Callable[[str], tuple[bool, str]]] = {
    "claimant_name": _nonempty,
    "policy_number": validate_policy_number,
    "date_of_loss": validate_date_of_loss,
    "location": _nonempty,
    "vehicle": _nonempty,
    "description": _nonempty,
    "injuries": normalize_yesno,
    "drivable": normalize_yesno,
}


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


def _ui_action(room: rtc.Room, component_id: str) -> Literal["mount", "update"]:
    mounted = getattr(room, "_awesome_voice_ui_mounted", None)
    if mounted is None:
        mounted = set()
        setattr(room, "_awesome_voice_ui_mounted", mounted)
    if component_id in mounted:
        return "update"
    mounted.add(component_id)
    return "mount"


def _claim_rows(claim: dict) -> list[dict]:
    return [{"label": FIELD_LABELS[f], "value": claim.get(f) or "-"} for f in FIELDS]


def _publish_claim(room: rtc.Room, claim: dict) -> None:
    publish_ui_event(
        room,
        "KeyValue",
        _ui_action(room, "claim"),
        component_id="claim",
        props={"title": "auto claim", "items": _claim_rows(claim)},
    )
    filled = sum(1 for f in FIELDS if claim.get(f))
    publish_ui_event(
        room,
        "Stat",
        _ui_action(room, "progress"),
        component_id="progress",
        props={"label": "captured", "value": filled, "of": len(FIELDS)},
    )


def _make_claim_ref() -> str:
    return f"CLM-{TODAY:%Y%m%d}-{uuid.uuid4().hex[:4].upper()}"


def _publish_filed(room: rtc.Room, claim: dict, ref: str) -> None:
    rows = _claim_rows(claim)
    rows.append({"label": "claim number", "value": ref, "accent": True})
    publish_ui_event(
        room,
        "KeyValue",
        _ui_action(room, "claim"),
        component_id="claim",
        props={"title": "auto claim", "items": rows},
    )
    publish_ui_event(
        room,
        "Card",
        _ui_action(room, "filed"),
        component_id="filed",
        props={
            "title": "claim filed",
            "body": f"{claim['claimant_name']} · {claim['vehicle']} · {claim['date_of_loss']}",
            "footer": ref,
            "accent": True,
        },
    )


INSTRUCTIONS = (
    "You are an auto insurance first notice of loss intake agent. Collect a car "
    "accident claim one field at a time, in this order: the caller's name, "
    "policy number, date of loss, where it happened, the vehicle (year, make, "
    "and model), what happened, whether anyone was injured, and whether the car "
    "is drivable. Ask for one field at a time and call record_field for each "
    "answer, using the exact field name. Pass each answer as the caller said it, "
    "like A B one two three four five six or yesterday: record_field checks and "
    "converts it, so never reject a value yourself. If record_field rejects "
    "a value, tell the caller the reason in one sentence and ask again. Never "
    "invent a value. When every field is recorded, read the whole claim back; "
    "once the caller confirms it is correct, call file_claim and tell them their "
    "claim number. Keep replies short, plain text, no markdown or emojis. Greet "
    "the caller once at the start."
)


class ClaimIntake(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room

    @function_tool()
    async def record_field(self, context: RunContext[dict], field: FieldName, value: str) -> str:
        """Record one field of the auto claim.

        Use the exact field name and the caller's own words. Spelled policy
        numbers and dates like "yesterday" or "June 28" are converted here.
        """
        ok, result = VALIDATORS[field](value)
        if not ok:
            return f"rejected: {result}. Ask the caller again."
        claim = context.userdata["claim"]
        claim[field] = result
        _publish_claim(self.room, claim)
        return f"recorded {field}"

    @function_tool()
    async def file_claim(self, context: RunContext[dict]) -> str:
        """File the claim. Refuses until every field has been recorded."""
        claim = context.userdata["claim"]
        missing = [f for f in FIELDS if not claim.get(f)]
        if missing:
            labels = ", ".join(FIELD_LABELS[f] for f in missing)
            return f"cannot file yet, still missing: {labels}"
        ref = context.userdata.get("claim_ref")
        if not ref:
            ref = _make_claim_ref()
            context.userdata["claim_ref"] = ref
        _publish_filed(self.room, claim, ref)
        return f"Filed. The claim number is {ref}."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = inference.VAD()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="claim-intake")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    userdata = {"claim": {}, "claim_ref": None}
    session = AgentSession(
        userdata=userdata,
        stt=assemblyai.STT(model="u3-rt-pro"),
        llm=google.LLM(model="gemini-3-flash-preview"),
        tts=inworld.TTS(model="inworld-tts-2", voice="Ashley"),
        vad=ctx.proc.userdata["vad"],
        turn_detection=inference.TurnDetector(),
    )

    await session.start(agent=ClaimIntake(ctx.room), room=ctx.room)
    await ctx.connect()
    _publish_claim(ctx.room, userdata["claim"])
    await session.generate_reply(
        instructions="Greet the caller and ask for their name to start the claim."
    )


if __name__ == "__main__":
    cli.run_app(server)
