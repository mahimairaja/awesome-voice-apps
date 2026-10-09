"""The loan application as plain data: questions, checkpoints and the carried context.

Nothing here talks to LiveKit. The agent records answers through these functions,
saves the whole state to a durable store after every write, and on a callback
rebuilds a short context message from it instead of replaying the old transcript.
"""

import math
import re
import secrets
from typing import Literal

LENDER = "Northbeam Capital"

FieldName = Literal[
    "owner_name",
    "business_name",
    "years_in_business",
    "annual_revenue",
    "loan_amount",
    "use_of_funds",
    "monthly_debt",
]

# (field, label shown on screen, the question the agent asks, kind)
QUESTIONS: list[tuple[str, str, str, str]] = [
    ("owner_name", "Owner", "What is your full name?", "text"),
    ("business_name", "Business", "What is the legal name of the business?", "text"),
    ("years_in_business", "Years trading", "How many years has it been operating?", "years"),
    ("annual_revenue", "Annual revenue", "What was last year's revenue, roughly?", "money"),
    ("loan_amount", "Amount", "How much would you like to borrow?", "money"),
    ("use_of_funds", "Use of funds", "What will the money be used for?", "text"),
    (
        "monthly_debt",
        "Monthly debt",
        "What does the business pay each month on existing debt?",
        "money",
    ),
]
FIELDS = [field for field, *_ in QUESTIONS]
LABELS = {field: label for field, label, *_ in QUESTIONS}
PROMPTS = {field: prompt for field, _, prompt, _ in QUESTIONS}
KINDS = {field: kind for field, *_, kind in QUESTIONS}

# Money and year limits keep made-up answers believable and catch mishearings.
RANGES = {
    "years": (0, 150),
    "annual_revenue": (1_000, 10_000_000_000),
    "loan_amount": (5_000, 5_000_000),
    "monthly_debt": (0, 100_000_000),
}
MAX_TEXT = 80
MAX_NOTE = 160
MAX_NOTES = 6
APP_ID = re.compile(r"^NB-[0-9A-F]{4}$")


def estimate_tokens(text: str) -> int:
    """About four characters per token for English; used only for comparisons."""
    return math.ceil(len(text) / 4)


def new_application(version: int = 0) -> dict:
    return {
        "app_id": f"NB-{secrets.token_hex(2).upper()}",
        "answers": {},
        "notes": [],
        "call": 1,
        "version": version,
        "transcript_tokens": 0,
        "submitted": None,
    }


def current(state: dict) -> str | None:
    """The question the caller is on: the first one without an answer."""
    return next((field for field in FIELDS if field not in state["answers"]), None)


def parse_amount(value: str) -> float | None:
    text = value.lower().replace(",", "").replace("$", "").strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(k|thousand|m|million|mm|b|billion)?", text)
    if not match:
        return None
    scale = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6}
    scale |= {"b": 1e9, "billion": 1e9}
    return float(match.group(1)) * scale.get(match.group(2) or "", 1)


def money(amount: float) -> str:
    return f"${amount:,.0f}"


def normalize(field: str, value: str) -> tuple[str | None, str]:
    """Return (stored value, problem). Stored values are display strings."""
    value = " ".join(str(value).split())
    if not value:
        return None, "the answer was empty"
    kind = KINDS[field]
    if kind == "text":
        if len(value) > MAX_TEXT:
            return None, f"keep it under {MAX_TEXT} characters"
        return value, ""
    amount = parse_amount(value)
    if amount is None:
        return None, "pass a plain number, like 250000"
    low, high = RANGES["years" if kind == "years" else field]
    if not low <= amount <= high:
        unit = "years" if kind == "years" else "dollars"
        return None, f"expected between {low:,} and {high:,} {unit}"
    if kind == "years":
        return f"{amount:g}", ""
    return money(amount), ""


def record(state: dict, field: str, value: str, note: str = "") -> str:
    """Store one answer and an optional note for later. The caller saves the state next."""
    if state["submitted"]:
        return "rejected: the application is already submitted"
    if field not in LABELS:
        return f"rejected: unknown field {field}"
    stored, problem = normalize(field, value)
    if stored is None:
        return f"rejected: {LABELS[field].lower()} {problem}"
    changed = field in state["answers"]
    state["answers"][field] = stored
    note = " ".join(str(note).split())[:MAX_NOTE]
    if note:
        # The rolling summary: one line per thing worth keeping, oldest dropped first.
        state["notes"] = [*state["notes"], note][-MAX_NOTES:]
    nxt = current(state)
    head = f"{'updated' if changed else 'recorded'} {LABELS[field].lower()}: {stored}."
    if nxt:
        return f"{head} Next ask: {PROMPTS[nxt]}"
    return f"{head} Every question is answered. Read back the amount and use, then ask to submit."


def decision(state: dict) -> str:
    answers = state["answers"]
    loan = parse_amount(answers["loan_amount"]) or 0
    revenue = parse_amount(answers["annual_revenue"]) or 1
    years = float(answers["years_in_business"])
    if years < 1:
        return "Referred to an underwriter: under a year of trading"
    if loan > revenue / 2:
        return "Referred to an underwriter: amount is over half of annual revenue"
    return "Ready for underwriting"


def submit(state: dict) -> str:
    if state["submitted"]:
        return f"already submitted as {state['app_id']}"
    missing = [LABELS[field].lower() for field in FIELDS if field not in state["answers"]]
    if missing:
        return f"rejected: still missing {', '.join(missing)}"
    state["submitted"] = {"ref": state["app_id"], "decision": decision(state)}
    return f"submitted. Reference {state['app_id']}. {state['submitted']['decision']}."


def context_message(state: dict) -> str:
    """The whole application in a few lines. Sent instead of the old transcript."""
    answered = "; ".join(
        f"{LABELS[f]}: {state['answers'][f]}" for f in FIELDS if f in state["answers"]
    )
    lines = [f"Application {state['app_id']}, call {state['call']}."]
    lines.append(f"Answered: {answered or 'nothing yet'}.")
    if state["notes"]:
        lines.append("Notes: " + " ".join(state["notes"]))
    nxt = current(state)
    if state["submitted"]:
        lines.append(f"Submitted: {state['submitted']['decision']}.")
    elif nxt:
        lines.append(f"Next question ({nxt}): {PROMPTS[nxt]}")
    else:
        lines.append("Every question is answered; ask to submit.")
    return "\n".join(lines)


def restore(saved: object) -> dict | None:
    """Accept a stored checkpoint only if every field has the shape this module writes."""
    if not isinstance(saved, dict):
        return None
    answers, notes = saved.get("answers"), saved.get("notes")
    submitted = saved.get("submitted")
    ints = [saved.get(key) for key in ("call", "version", "transcript_tokens")]
    if (
        not isinstance(saved.get("app_id"), str)
        or not APP_ID.match(saved["app_id"])
        or not isinstance(answers, dict)
        or not all(field in LABELS and isinstance(v, str) for field, v in answers.items())
        or any(len(v) > MAX_TEXT for v in answers.values())
        or not isinstance(notes, list)
        or len(notes) > MAX_NOTES
        or not all(isinstance(n, str) and len(n) <= MAX_NOTE for n in notes)
        or not all(type(n) is int and 0 <= n < 10**9 for n in ints)
        or not (
            submitted is None
            or (
                isinstance(submitted, dict)
                and isinstance(submitted.get("ref"), str)
                and isinstance(submitted.get("decision"), str)
            )
        )
    ):
        return None
    return {
        "app_id": saved["app_id"],
        "answers": dict(answers),
        "notes": list(notes),
        "call": saved["call"],
        "version": saved["version"],
        "transcript_tokens": saved["transcript_tokens"],
        "submitted": submitted,
    }


def resume(saved: object) -> tuple[dict, bool]:
    """Start the next call: continue an open application, or open a new one."""
    state = restore(saved)
    if state is None:
        return new_application(), False
    if state["submitted"]:
        # A finished application is not resumed; the next one keeps counting versions.
        return new_application(state["version"]), False
    state["call"] += 1
    return state, True


def snapshot(state: dict) -> dict:
    """The panel's view of the application. Small and free of anything secret."""
    nxt = current(state)
    return {
        "appId": state["app_id"],
        "call": state["call"],
        "version": state["version"],
        "questions": [
            {
                "field": field,
                "label": LABELS[field],
                "value": state["answers"].get(field, ""),
                "status": "done"
                if field in state["answers"]
                else "current"
                if field == nxt and not state["submitted"]
                else "todo",
            }
            for field in FIELDS
        ],
        "notes": state["notes"],
        "submitted": state["submitted"],
    }
