"""Tool dispatch may lead the final transcript; never treat dispatch as consent."""

import asyncio
import json

import pytest
from agent import SalesAssistant


@pytest.mark.asyncio
async def test_booking_waits_for_final_confirmation():
    assistant = SalesAssistant()
    assistant.hear("Monday with the technical lead")
    assistant.booking.update("voice", "monday", "technical lead")
    await assistant.booking.availability(assistant.booking.revision, 0)
    await assistant.propose_meeting(assistant.booking.revision, "Monday 10:00")
    task = asyncio.create_task(assistant.book_meeting(assistant.booking.revision, "Monday 10:00"))
    await asyncio.sleep(0)
    assert not task.done()
    assistant.hear("Confirm")
    assert json.loads(await task)["status"] == "booked"


@pytest.mark.asyncio
async def test_pending_booking_rejects_a_correction():
    assistant = SalesAssistant()
    assistant.hear("Monday with the technical lead")
    assistant.booking.update("voice", "monday", "technical lead")
    await assistant.booking.availability(assistant.booking.revision, 0)
    await assistant.propose_meeting(assistant.booking.revision, "Monday 10:00")
    task = asyncio.create_task(assistant.book_meeting(assistant.booking.revision, "Monday 10:00"))
    await asyncio.sleep(0)
    assistant.hear("Actually Friday")
    assert json.loads(await task)["status"] == "rejected"
    assert assistant.booking.booked is None
