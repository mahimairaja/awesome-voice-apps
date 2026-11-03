"""In-memory simulation only. No calendar, CRM, email, or external writes."""

import asyncio
import re
from dataclasses import dataclass, field

SLOTS = {
    "monday": ["Monday 10:00", "Monday 14:00"],
    "friday": ["Friday 11:00", "Friday 15:00"],
}
CONFIRM = re.compile(r"(?:yes|yes please|confirm|please confirm|go ahead|book it)[.!?]*", re.I)
TIMEZONE = "America/Toronto"


def spoken(slot: str) -> str:
    """The slot as the voice reads it back: 'Friday 15:00' is 'Friday at 3 p.m. Toronto time'."""
    day, clock = slot.split()
    hour = int(clock.split(":")[0])
    return f"{day} at {hour % 12 or 12} {'a.m.' if hour < 12 else 'p.m.'} Toronto time"


@dataclass
class Booking:
    need: str = ""
    day: str = ""
    attendees: str = ""
    revision: int = 0
    user_turn: int = 0
    request_turn: int = -1
    latest_text: str = ""
    offered: list[str] = field(default_factory=list)
    offered_turn: int = -1
    offered_revision: int = -1
    proposed: str = ""
    booked: dict | None = None

    def hear(self, text: str) -> None:
        self.user_turn += 1
        self.latest_text = text.strip()
        if not CONFIRM.fullmatch(self.latest_text):
            self.proposed = ""

    def update(self, need: str, day: str, attendees: str) -> dict:
        day = day.lower().strip()
        if self.booked:
            return {"status": "already_booked", "booking": self.booked}
        self.request_turn = self.user_turn
        corrected = bool(self.day) and day != self.day
        if (need, day, attendees) != (self.need, self.day, self.attendees):
            self.need, self.day, self.attendees = need, day, attendees
            self.revision += 1
            self.offered.clear()
            self.proposed = ""
            self.offered_revision = -1
        if corrected:
            # A day correction re-queries the new day at once, so no lookup for the
            # old day can be read back as current and the caller hears the new slots.
            return {"revision": self.revision, "need": self.need, **self._offer(self.revision)}
        return {"revision": self.revision, "need": self.need, "day": self.day}

    def _offer(self, revision: int) -> dict:
        self.offered = list(SLOTS.get(self.day, []))
        self.offered_revision = revision
        self.offered_turn = self.user_turn
        return {
            "status": "available",
            "revision": revision,
            "day": self.day,
            "slots": self.offered,
            "read_back": [spoken(slot) for slot in self.offered],
            "timezone": TIMEZONE,
            "simulation": True,
        }

    async def availability(self, revision: int, delay: float = 2) -> dict:
        turn = self.user_turn
        if self.request_turn != turn:
            return {
                "status": "superseded",
                "message": "Update the request from the latest caller transcript before looking again.",
            }
        await asyncio.sleep(delay)
        # Any newer caller turn makes this lookup stale. Let the backend interpret
        # the new request before running a fresh lookup; never guess corrections.
        if revision != self.revision or turn != self.user_turn:
            return {"status": "superseded", "message": "Caller spoke again; check latest request."}
        return self._offer(revision)

    def propose(self, revision: int, slot: str) -> dict:
        if (
            revision != self.revision
            or revision != self.offered_revision
            or slot not in self.offered
            or self.user_turn != self.offered_turn
        ):
            return {"status": "rejected", "reason": "Check current availability first."}
        self.proposed = slot
        self.offered_turn = self.user_turn
        return {
            "status": "awaiting_confirmation",
            "slot": slot,
            "read_back": spoken(slot),
            "timezone": TIMEZONE,
            "message": "Read read_back exactly and ask the caller to say confirm.",
        }

    def confirm(self, revision: int, slot: str) -> dict:
        if self.booked:
            # A retry cannot create a second meeting, or silently change the first.
            return {"status": "already_booked", "booking": self.booked}
        if revision != self.revision or revision != self.offered_revision:
            return {"status": "rejected", "reason": "Stale request. Check availability again."}
        if slot not in self.offered or slot != self.proposed:
            return {"status": "rejected", "reason": "Slot was not offered."}
        if self.user_turn <= self.offered_turn or not CONFIRM.fullmatch(self.latest_text):
            return {
                "status": "needs_confirmation",
                "reason": "Read one exact slot, then ask the caller to say 'confirm'.",
            }
        self.booked = {
            "id": "SIM-001",
            "slot": slot,
            "timezone": TIMEZONE,
            "need": self.need,
            "attendees": self.attendees,
            "simulation": True,
        }
        return {"status": "booked", "booking": self.booked}
