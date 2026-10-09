"""Consented cross-call memory: what a voice agent keeps, why, and for how long.

Pure logic, no LiveKit imports, so the policy is testable on its own. A client
file is a list of entries. Each entry has a kind, the note, the reason it was
kept, and an expiry set from the kind, never by the model:

    {"id": "m3f2a1", "kind": "task", "text": "...", "why": "...",
     "saved": 1760000000, "expires": 1760604800, "forgotten": None, "by": None}

Forgetting deletes the text and the reason and keeps a tombstone (id, kind,
when, by whom), so the caller can see that it happened.
"""

import re
import secrets
from typing import Literal

BANK = "Corvel Private"

DAY = 86_400
# Retention by kind. The model picks the kind; the expiry follows from it.
TTL = {
    "consent": 30 * DAY,
    "summary": 30 * DAY,
    "preference": 30 * DAY,
    "task": 7 * DAY,
    "context": 3 * DAY,
}
NoteKind = Literal["preference", "task", "context"]
NOTE_KINDS = ("preference", "task", "context")
MAX_NOTE = 160
MAX_WHY = 120
MAX_SUMMARY = 400
# Active notes per caller, consent and summary aside. A file is not a transcript.
MAX_ACTIVE = 8

# Never kept, whatever the caller asks. Checked before anything is written.
NEVER_KEPT = (
    (re.compile(r"\d(?:[\s.-]?\d){5,}"), "account, card or phone numbers"),
    (
        re.compile(
            r"\b(pins?|passwords?|passcodes?|security (?:questions?|answers?)|"
            r"one[- ]time (?:code|password)|otp|cvv|cvc)\b",
            re.IGNORECASE,
        ),
        "credentials",
    ),
    (
        re.compile(
            r"\b(sin|ssn|social (?:insurance|security)(?: number)?|passport number|"
            r"driver'?s licen[cs]e number)\b",
            re.IGNORECASE,
        ),
        "government ID numbers",
    ),
)
_NUMBER_RUN = NEVER_KEPT[0][0]


def screen(note: str) -> str | None:
    """Why this note may not be kept, or None if it may."""
    for pattern, reason in NEVER_KEPT:
        if pattern.search(note):
            return reason
    return None


def scrub(text: str) -> str:
    """Mask long number runs, for text that is kept anyway (summaries, refusals)."""
    return _NUMBER_RUN.sub("[number removed]", text)


def clean(text: str, limit: int) -> str:
    return " ".join(str(text).split())[:limit]


def new_id(entries: list[dict]) -> str:
    taken = {e["id"] for e in entries}
    while True:
        candidate = "m" + secrets.token_hex(3)
        if candidate not in taken:
            return candidate


def is_active(entry: dict, now: float) -> bool:
    return entry["forgotten"] is None and entry["expires"] > now


def active(entries: list[dict], now: float) -> list[dict]:
    return [e for e in entries if is_active(e, now)]


def consent(entries: list[dict], now: float) -> dict | None:
    return next((e for e in active(entries, now) if e["kind"] == "consent"), None)


def summary(entries: list[dict], now: float) -> dict | None:
    found = [e for e in active(entries, now) if e["kind"] == "summary"]
    return max(found, key=lambda e: e["saved"]) if found else None


def notes(entries: list[dict], now: float) -> list[dict]:
    return [e for e in active(entries, now) if e["kind"] in NOTE_KINDS]


def tombstone(entry: dict, now: float, by: str) -> dict:
    return {**entry, "text": None, "why": None, "forgotten": int(now), "by": by}


def expire(entries: list[dict], now: float) -> list[dict]:
    """Expired entries become tombstones: the text is gone, the record stays."""
    return [
        tombstone(e, e["expires"], "expiry")
        if e["forgotten"] is None and e["expires"] <= now
        else e
        for e in entries
    ]


def add(entries: list[dict], kind: str, text: str, why: str, now: float) -> list[dict]:
    """Return the file with one more entry. Raises ValueError with the reason if refused."""
    if kind not in TTL:
        raise ValueError(f"unknown kind {kind}")
    limit = MAX_SUMMARY if kind == "summary" else MAX_NOTE
    text = clean(text, limit)
    why = clean(why, MAX_WHY)
    if not text:
        raise ValueError("empty note")
    if kind == "summary":
        text = scrub(text)
    elif reason := screen(text):
        raise ValueError(f"never kept: {reason}")
    entries = expire(entries, now)
    if kind != "consent" and not consent(entries, now):
        raise ValueError("no consent on file")
    if kind == "consent" and consent(entries, now):
        return entries
    if kind in NOTE_KINDS and len(notes(entries, now)) >= MAX_ACTIVE:
        raise ValueError("the client file is full; forget something first")
    if kind == "summary":
        # Only the latest summary is kept; the older one is replaced, not stacked.
        entries = [
            tombstone(e, now, "replaced") if e["kind"] == "summary" and is_active(e, now) else e
            for e in entries
        ]
    entry = {
        "id": new_id(entries),
        "kind": kind,
        "text": text,
        "why": why,
        "saved": int(now),
        "expires": int(now) + TTL[kind],
        "forgotten": None,
        "by": None,
    }
    return [*entries, entry]


def forget(entries: list[dict], memory_id: str, now: float, by: str) -> list[dict]:
    """Forget one entry. Withdrawing consent forgets everything with it."""
    target = next((e for e in entries if e["id"] == memory_id and is_active(e, now)), None)
    if not target:
        raise ValueError(f"no active memory {memory_id}")
    if target["kind"] == "consent":
        return forget_all(entries, now, by)
    return [tombstone(e, now, by) if e is target else e for e in entries]


def forget_all(entries: list[dict], now: float, by: str) -> list[dict]:
    return [tombstone(e, now, by) if is_active(e, now) else e for e in entries]


def when(seconds: float) -> str:
    """How long until an expiry, in the units a caller would use."""
    seconds = max(0, int(seconds))
    if seconds >= 2 * DAY:
        return f"{round(seconds / DAY)} days"
    if seconds >= 2 * 3600:
        return f"{round(seconds / 3600)} hours"
    minutes = max(1, round(seconds / 60))
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


def context_block(entries: list[dict], now: float) -> str:
    """What the model is told about this caller at the start of a call."""
    if not consent(entries, now):
        return (
            " Client file: no consent on file, so nothing from earlier calls is "
            "available. Ask before you keep anything."
        )
    lines = [" Client file, from earlier calls (verified by caller ID):"]
    last = summary(entries, now)
    if last:
        lines.append(f" Last call: {last['text']}")
    kept = notes(entries, now)
    if kept:
        lines.append(" Notes, by id:")
        lines += [f" [{e['id']}] {e['kind']}: {e['text']}" for e in kept]
    if not last and not kept:
        lines.append(" Consent is on file but nothing has been kept yet.")
    lines.append(
        " Use the file naturally, never read ids or expiry dates aloud unless asked, "
        "and do not ask again for anything already in it."
    )
    return "".join(lines)


def is_returning(entries: list[dict], now: float) -> bool:
    return bool(consent(entries, now) and (summary(entries, now) or notes(entries, now)))


def snapshot(state: dict, now: float) -> dict:
    """The panel's view: what is kept, why, until when, and what was not."""
    entries = state["entries"]
    on_file = consent(entries, now)
    last = summary(entries, now)
    fresh = state.get("fresh", set())

    def row(e: dict) -> dict:
        return {
            "id": e["id"],
            "kind": e["kind"],
            "text": e["text"],
            "why": e["why"],
            "saved": e["saved"],
            "expires": e["expires"],
            "fresh": e["id"] in fresh,
        }

    # A replaced summary is housekeeping, not something the caller deleted.
    forgotten = sorted(
        (e for e in entries if e["forgotten"] is not None and e["by"] != "replaced"),
        key=lambda e: e["forgotten"],
        reverse=True,
    )
    return {
        "consent": {"saved": on_file["saved"], "expires": on_file["expires"]} if on_file else None,
        "summary": row(last) if last else None,
        "memories": [row(e) for e in notes(entries, now)],
        "forgotten": [
            {"id": e["id"], "kind": e["kind"], "at": e["forgotten"], "by": e["by"]}
            for e in forgotten[:4]
        ],
        "refused": state.get("refused", [])[-3:],
        "loaded": state.get("loaded", 0),
    }
