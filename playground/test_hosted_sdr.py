import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hosted_sdr import HostedSDR, _module, meeting_state, tool_calls


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

    async def test_tool_events_carry_calendar_facts_only(self):
        booked = {
            "status": "booked",
            "booking": {"id": "SIM-001", "slot": "Monday 10:00", "need": "private project"},
        }
        event = SimpleNamespace(
            function_calls=[
                SimpleNamespace(
                    call_id="c1",
                    name="update_request",
                    arguments=json.dumps(
                        {"need": "private project", "day": "monday", "attendees": "private"}
                    ),
                ),
                SimpleNamespace(
                    call_id="c2",
                    name="book_meeting",
                    arguments=json.dumps({"revision": 2, "slot": "Monday 10:00"}),
                ),
                SimpleNamespace(call_id="c3", name="unknown_tool", arguments="{}"),
            ],
            function_call_outputs=[
                SimpleNamespace(
                    output=json.dumps({"revision": 2, "need": "private"}), is_error=False
                ),
                SimpleNamespace(output=json.dumps(booked), is_error=False),
                None,
            ],
        )
        calls = tool_calls(event)
        self.assertEqual([call["name"] for call in calls], ["update_request", "book_meeting"])
        self.assertEqual(calls[0]["input"], {"day": "monday"})
        self.assertEqual(calls[1]["output"]["booking"], {"slot": "Monday 10:00"})
        self.assertNotIn("private", json.dumps(calls))

    async def test_native_response_guard_stops_excess_delegation(self):
        finish = AsyncMock()
        pending = []
        agent = HostedSDR(SimpleNamespace(), {}, finish, pending.append, None)
        for _ in range(17):
            agent.budget_event({"type": "response.event", "event": {"type": "response.created"}})
        self.assertEqual(len(pending), 1)
        await pending[0]
        finish.assert_awaited_once()

    async def test_day_correction_reads_back_a_friday_slot_in_toronto_time(self):
        agent = HostedSDR(SimpleNamespace(), {}, AsyncMock(), lambda coro: coro.close(), None)
        agent.hear("Can we meet with your team Monday morning?")
        await agent.update_request("dental booking agent", "monday", "CTO")
        agent.hear("Actually, Friday works better than Monday.")
        result = json.loads(await agent.update_request("dental booking agent", "friday", "CTO"))
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["slots"], ["Friday 11:00", "Friday 15:00"])
        proposal = json.loads(await agent.propose_meeting(result["revision"], "Friday 11:00"))
        self.assertEqual(proposal["read_back"], "Friday at 11 a.m. Toronto time")
        self.assertEqual(meeting_state(agent.booking)["day"], "friday")

    async def test_instances_do_not_share_booking_state(self):
        a = HostedSDR(SimpleNamespace(), {}, AsyncMock(), asyncio.create_task, None)
        b = HostedSDR(SimpleNamespace(), {}, AsyncMock(), asyncio.create_task, None)
        a.booking.update("project", "monday", "one")
        self.assertEqual(b.booking.day, "")
