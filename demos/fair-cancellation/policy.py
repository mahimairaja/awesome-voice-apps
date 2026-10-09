"""Pure policy engine for the fair cancellation line: no LiveKit, no network.

The model talks; this module decides. Every rule a regulator would ask about
is code here, so it can be unit tested without a model and cannot be argued
away by a caller or forgotten by a prompt:

- CANCEL.INTENT    Code, not the model, hears "cancel" and starts the clock.
- OFFER.MATCH      At most one retention offer, picked here from the reason.
- OFFER.DISCLOSE   Before an offer, code says the caller can say cancel any time.
- OFFER.CONSENT    An offer counts as accepted only after a clear yes.
- SPEECH.SCREEN    Sentences that pitch an unapproved deal or guilt the caller
                   are dropped before they reach the voice.
- CANCEL.DEADLINE  A decline, or three caller turns, and code cancels.
- CANCEL.CONFIRM   The confirmation number is spoken by code, every time.
"""

import random
import re
import uuid
from datetime import date, datetime, timedelta

BRAND = "Lumora+"
SPOKEN_BRAND = "Lumora Plus"
# Caller turns after the first cancel request before code cancels on its own.
CANCEL_WITHIN = 3
MAX_OFFERS = 1
LOG_LIMIT = 8

REASONS = ("price", "not_watching", "switching", "other")
# One fair offer per reason, or none. The model can ask for "the offer"; it can
# never name its own price.
OFFERS: dict[str, dict | None] = {
    "price": {
        "id": "half_off",
        "label": "50% off for 3 months",
        "spoken": "half price for the next three months",
    },
    "not_watching": {
        "id": "pause",
        "label": "Pause free for 2 months",
        "spoken": "a free two-month pause, with nothing charged and your profiles kept",
    },
    "switching": None,
    "other": None,
}
PLANS = [("Premium", "22.99"), ("Standard", "15.99"), ("Premium", "24.99")]

DISCLOSURE = "Before I mention anything else: you can say cancel at any time and I will."

_CANCEL = re.compile(
    r"\b(cancel\w*|unsubscribe|close (?:my|the) account|"
    r"end (?:my|the|this) (?:subscription|membership|plan|account)|"
    r"stop (?:my|the) (?:subscription|membership|billing|plan))\b",
    re.IGNORECASE,
)
_NEGATED = re.compile(
    r"\b(?:don'?t|do not|not|never|no need to)\s+(?:\w+\s+)?cancel", re.IGNORECASE
)
_DECLINE = re.compile(
    r"^\W*(?:no|nope|nah)\b|not interested|no thanks|no thank you|i'?m sure|"
    r"i don'?t want (?:it|that|the offer)",
    re.IGNORECASE,
)
_YES = re.compile(
    r"\b(yes|yeah|yep|sure|okay|ok|sounds good|i'?ll take|let'?s do|deal|that works|"
    r"go ahead|please do)\b",
    re.IGNORECASE,
)
# A sentence that pitches a deal the engine did not approve, or pressures the caller.
_PITCH = re.compile(
    r"\d+\s?%|\bpercent\b|\bdiscount|\bhalf (?:off|price)\b|\bfree (?:month|trial|pause)|"
    r"\bpause\b|\bpromo|\bcredit\b|\bspecial (?:offer|deal|price)|\bdeal\b",
    re.IGNORECASE,
)
_PRESSURE = re.compile(
    r"you'?ll lose|you will lose|are you (?:really |absolutely )?sure|before you go|"
    r"we'?d hate to|we would hate to|reconsider|last chance|miss out",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"(\d+)\s?(?:%|percent)", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Twelve letters and digits that do not sound alike on a phone line.
_REF_ALPHABET = "3479ACFHKMRX"


def new_account(rng: random.Random | None = None, today: date | None = None) -> dict:
    rng = rng or random.Random()
    today = today or datetime.now().astimezone().date()
    plan, price = rng.choice(PLANS)
    renews = today + timedelta(days=rng.randint(6, 24))
    return {
        "brand": BRAND,
        "plan": plan,
        "price": price,
        "since": str(today.year - rng.randint(1, 5)),
        "renews": renews.isoformat(),
        "profiles": rng.randint(2, 5),
    }


def initial_state(account: dict | None = None, today: date | None = None) -> dict:
    return {
        "account": account or new_account(today=today),
        "turn": 0,
        "intent_turn": None,
        "reason": None,
        "offers": [],
        "declined": False,
        "due": [],
        "outcome": None,
        "log": [],
    }


def _log(state: dict, rule: str, verdict: str, text: str) -> None:
    entry = {"turn": state["turn"], "rule": rule, "verdict": verdict, "text": text[:160]}
    state["log"] = [*state["log"], entry][-LOG_LIMIT:]


def wants_cancel(text: str) -> bool:
    return bool(_CANCEL.search(text)) and not _NEGATED.search(text)


def says_yes(text: str) -> bool:
    return bool(_YES.search(text)) and not wants_cancel(text) and not _DECLINE.search(text)


def _open_offer(state: dict) -> dict | None:
    return next((o for o in state["offers"] if o["status"] == "offered"), None)


def turns_left(state: dict) -> int | None:
    if state["intent_turn"] is None:
        return None
    return max(0, CANCEL_WITHIN - (state["turn"] - state["intent_turn"]))


def observe_caller(state: dict, text: str) -> bool:
    """Run on every finished caller turn, before the model. True means cancel now."""
    if state["outcome"]:
        return False
    state["turn"] += 1
    if state["intent_turn"] is None:
        if wants_cancel(text):
            state["intent_turn"] = state["turn"]
            _log(
                state,
                "CANCEL.INTENT",
                "pass",
                f"Cancel request heard by code. Clock started: honor within {CANCEL_WITHIN} turns.",
            )
        return False
    offer = _open_offer(state)
    if offer and (wants_cancel(text) or _DECLINE.search(text)):
        offer["status"] = "declined"
        state["declined"] = True
        _log(state, "CANCEL.DEADLINE", "enforce", "Offer declined. Policy cancels now.")
        return True
    if offer and says_yes(text):
        # A late yes still wins: the model applies the offer they asked for.
        return False
    if turns_left(state) == 0:
        _log(
            state,
            "CANCEL.DEADLINE",
            "enforce",
            f"{CANCEL_WITHIN} turns since the request. Policy cancels without the model.",
        )
        return True
    return False


def note_reason(state: dict, reason: str) -> str:
    if reason not in REASONS:
        return f"rejected: reason must be one of {', '.join(REASONS)}"
    if state["intent_turn"] is None:
        return "noted. The caller has not asked to cancel; just help them."
    state["reason"] = reason
    offer = OFFERS[reason]
    if not offer or state["offers"]:
        return f"noted {reason}. No offer applies: call cancel_membership now."
    return f"noted {reason}. One offer applies: call make_offer, or cancel if they decline."


def make_offer(state: dict) -> str:
    """The one retention offer, if the policy allows it. Returns the tool result."""
    if state["outcome"]:
        return "blocked: the call already has an outcome. Do not pitch anything."
    if state["intent_turn"] is None:
        _log(state, "OFFER.MATCH", "block", "Offer refused: the caller never asked to cancel.")
        return "blocked: the caller has not asked to cancel. Do not pitch offers."
    if len(state["offers"]) >= MAX_OFFERS or state["declined"]:
        _log(state, "OFFER.MATCH", "block", "Second offer refused. One fair offer per call.")
        return "blocked: the one offer is used. Call cancel_membership now."
    if state["reason"] is None:
        return "ask why they are leaving in one short question, then call note_reason."
    offer = OFFERS[state["reason"]]
    if not offer:
        _log(
            state,
            "OFFER.MATCH",
            "block",
            f"No fair offer for '{state['reason'].replace('_', ' ')}'. Straight to cancel.",
        )
        return "blocked: no offer fits this reason. Call cancel_membership now."
    state["offers"].append({"id": offer["id"], "label": offer["label"], "status": "offered"})
    state["due"].append("disclosure")
    _log(state, "OFFER.MATCH", "pass", f"Offer approved: {offer['label']} (1 of {MAX_OFFERS}).")
    return (
        f"approved: {offer['spoken']}. The policy already tells the caller they can say "
        "cancel at any time. Offer it in one sentence, then ask if they want it or still "
        "want to cancel. No other deals and no pressure."
    )


def accept_offer(state: dict, caller_said: str) -> str:
    offer = _open_offer(state)
    if not offer or state["outcome"]:
        return "blocked: there is no open offer to accept."
    if not says_yes(caller_said):
        _log(state, "OFFER.CONSENT", "block", "Acceptance refused: no clear yes from the caller.")
        return "blocked: the caller has not clearly said yes. Ask once: keep the offer, or cancel?"
    offer["status"] = "accepted"
    state["outcome"] = {
        "kind": "retained",
        "ref": _ref("KP"),
        "effective": state["account"]["renews"],
        "by": "caller",
    }
    _log(state, "OFFER.CONSENT", "pass", f"Caller said yes. {offer['label']} applied.")
    state["due"].append("retained")
    return "applied. The policy reads the reference. Say one short goodbye sentence."


def cancel(state: dict, by: str = "agent") -> str:
    """Cancel the membership. Idempotent; refuses without a cancel request."""
    if state["outcome"] and state["outcome"]["kind"] == "cancelled":
        return "already cancelled. Say one short goodbye sentence."
    if state["intent_turn"] is None:
        _log(state, "CANCEL.INTENT", "block", "Cancel refused: the caller never asked.")
        return "blocked: the caller has not asked to cancel."
    if state["outcome"]:
        return "blocked: the caller kept the membership with the offer."
    offer = _open_offer(state)
    if offer:
        offer["status"] = "declined"
    used = state["turn"] - state["intent_turn"]
    state["outcome"] = {
        "kind": "cancelled",
        "ref": _ref("CX"),
        "effective": state["account"]["renews"],
        "by": by,
    }
    who = "the agent" if by == "agent" else "the policy"
    _log(
        state,
        "CANCEL.CONFIRM",
        "pass",
        f"Cancelled by {who} {used} of {CANCEL_WITHIN} turns after the request. No fee.",
    )
    state["due"].append("cancelled")
    return "cancelled. The policy reads the confirmation number. Say one short goodbye sentence."


def _ref(prefix: str) -> str:
    raw = uuid.uuid4().int
    chars = "".join(_REF_ALPHABET[(raw >> (5 * i)) % len(_REF_ALPHABET)] for i in range(6))
    return f"{prefix}-{chars[:3]}-{chars[3:]}"


def spell(ref: str) -> str:
    """CX-4K7-AR9 -> "C, X. 4, K, 7. A, R, 9": one character at a time."""
    return ". ".join(", ".join(part) for part in ref.split("-"))


def _spoken_date(iso: str) -> str:
    day = date.fromisoformat(iso)
    n = day.day
    suffix = "th" if 11 <= n <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{day.strftime('%B')} {n}{suffix}"


def take_script(state: dict) -> str:
    """Required words the policy speaks itself, ahead of the model's next reply."""
    out = []
    for item in state["due"]:
        if item == "disclosure":
            out.append(DISCLOSURE)
            _log(state, "OFFER.DISCLOSE", "pass", "Disclosure spoken by code before the offer.")
        elif item in ("cancelled", "retained") and state["outcome"]:
            o = state["outcome"]
            until = _spoken_date(o["effective"])
            if item == "cancelled":
                out.append(
                    f"Your {SPOKEN_BRAND} membership is cancelled. Your confirmation number is "
                    f"{spell(o['ref'])}. You keep access until {until} and you will not be "
                    "charged again."
                )
            else:
                out.append(
                    f"Your offer is applied from {until}. Your reference is {spell(o['ref'])}."
                )
    state["due"] = []
    return " ".join(out)


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Complete sentences ready to screen, and the unfinished tail."""
    parts = _SENTENCE_END.split(buffer)
    if len(parts) == 1:
        return [], buffer
    return [p + " " for p in parts[:-1]], parts[-1]


def _approved(state: dict, sentence: str) -> bool:
    """A pitch is fine only while the approved offer is live, and only at its numbers."""
    live = [o for o in state["offers"] if o["status"] in ("offered", "accepted")]
    if not live:
        return False
    allowed = set(_PERCENT.findall(live[0]["label"]))
    return set(_PERCENT.findall(sentence)) <= allowed


def screen(state: dict, sentence: str) -> str:
    """Drop a sentence that pressures the caller or pitches a deal the policy did not
    approve. Returns the sentence to speak (empty when dropped)."""
    if not sentence.strip():
        return sentence
    rule = None
    if state["intent_turn"] is not None and _PRESSURE.search(sentence):
        rule = "Dropped a pressure line"
    elif _PITCH.search(sentence) and not _approved(state, sentence):
        rule = "Dropped an unapproved offer"
    if not rule:
        return sentence
    _log(state, "SPEECH.SCREEN", "block", f"{rule}: “{sentence.strip()[:90]}”")
    return ""


def snapshot(state: dict) -> dict:
    """Everything the panel draws, in one UI event."""
    left = turns_left(state)
    outcome = state["outcome"]
    if outcome:
        clock = outcome["kind"] if outcome["by"] != "policy" else "enforced"
    elif state["intent_turn"] is None:
        clock = "idle"
    else:
        clock = "running"
    return {
        "account": dict(state["account"]),
        "clock": {
            "limit": CANCEL_WITHIN,
            "used": 0 if left is None else CANCEL_WITHIN - left,
            "state": clock,
        },
        "reason": state["reason"],
        "offers": [dict(o) for o in state["offers"]],
        "log": [dict(e) for e in state["log"]],
        "outcome": dict(outcome) if outcome else None,
    }
