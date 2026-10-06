import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hosted_sdr import HostedSDR, meeting_state, _module


class SDRSafety(unittest.IsolatedAsyncioTestCase):
    async def test_ui_excludes_private_details_and_confirms_only_after_new_turn(self):
        booking = _module.Booking()
        booking.hear("Monday for my private project")
        booking.update("private project", "monday", "private person")
        await booking.availability(booking.revision, delay=0)
        booking.propose(booking.revision, "Monday 10:00")
        self.assertFalse(meeting_state(booking)["booked"])
        self.assertNotIn("private", json.dumps(meeting_state(booking)))
        booking.hear("confirm")
        booking.confirm(booking.revision, "Monday 10:00")
        self.assertEqual(
            meeting_state(booking), {"day": "monday", "slot": "Monday 10:00", "booked": True}
        )

    async def test_native_response_guard_stops_excess_delegation(self):
        finish = AsyncMock()
        pending = []
        agent = HostedSDR(SimpleNamespace(), {}, finish, pending.append, None)
        for _ in range(17):
            agent.budget_event({"type": "response.event", "event": {"type": "response.created"}})
        self.assertEqual(len(pending), 1)
        await pending[0]
        finish.assert_awaited_once()

    async def test_instances_do_not_share_booking_state(self):
        a = HostedSDR(SimpleNamespace(), {}, AsyncMock(), asyncio.create_task, None)
        b = HostedSDR(SimpleNamespace(), {}, AsyncMock(), asyncio.create_task, None)
        a.booking.update("project", "monday", "one")
        self.assertEqual(b.booking.day, "")
