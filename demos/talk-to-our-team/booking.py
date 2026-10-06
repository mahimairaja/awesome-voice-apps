"""In-memory simulation only. No calendar, CRM, email, or external writes."""

import asyncio
import re
from dataclasses import dataclass, field

SLOTS = {
    "monday": ["Monday 10:00", "Monday 14:00"],
    "friday": ["Friday 11:00", "Friday 15:00"],
}
CONFIRM = re.compile(r"(?:yes|yes please|confirm|please confirm|go ahead|book it)[.!?]*", re.I)


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
        if (need, day, attendees) != (self.need, self.day, self.attendees):
            self.need, self.day, self.attendees = need, day, attendees
            self.revision += 1
            self.offered.clear()
            self.proposed = ""
            self.offered_revision = -1
        return {"revision": self.revision, "need": self.need, "day": self.day}

    async def availability(self, revision: int, delay: float = 2) -> dict:
        turn = self.user_turn
        if self.request_turn != turn:
            return {
                "status": "superseded",
                "message": "Update the request from the latest caller transcript before looking again.",
            }
        day = self.day
        await asyncio.sleep(delay)
        # Any newer caller turn makes this lookup stale. Let the backend interpret
        # the new request before running a fresh lookup; never guess corrections.
        if revision != self.revision or turn != self.user_turn:
            return {"status": "superseded", "message": "Caller spoke again; check latest request."}
        self.offered = list(SLOTS.get(day, []))
        self.offered_revision = revision
        self.offered_turn = turn
        return {
            "status": "available",
            "revision": revision,
            "slots": self.offered,
            "timezone": "America/Toronto",
            "simulation": True,
        }

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
            "timezone": "America/Toronto",
            "message": "Read this slot and ask for confirm.",
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
            "timezone": "America/Toronto",
            "need": self.need,
            "attendees": self.attendees,
            "simulation": True,
        }
        return {"status": "booked", "booking": self.booked}
