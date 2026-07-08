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

import asyncio  # noqa: F401
import json  # noqa: F401
import logging
import re
import uuid  # noqa: F401
from datetime import date, datetime
from typing import Callable, Literal

from dotenv import load_dotenv
from livekit import rtc  # noqa: F401
from livekit.agents import (  # noqa: F401
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
from livekit.plugins import assemblyai, google, inworld  # noqa: F401

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
_YES = {"yes", "y", "yeah", "yep", "yup", "true", "correct", "affirmative"}
_NO = {"no", "n", "nope", "nah", "false", "negative"}


def _nonempty(value: str) -> tuple[bool, str]:
    v = value.strip()
    return (True, v) if v else (False, "that came through empty; ask again")


def validate_policy_number(value: str) -> tuple[bool, str]:
    v = value.strip().upper().replace(" ", "").replace("-", "")
    if POLICY_RE.match(v):
        return True, v
    return False, "a policy number is two letters then six to eight digits, like AB123456"


def validate_date_of_loss(value: str, today: date | None = None) -> tuple[bool, str]:
    today = today or TODAY
    try:
        d = datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return False, "give the date of loss as year-month-day, like 2026-06-30"
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
