import asyncio

import pytest
from booking import Booking, spoken


async def offer(b):
    b.hear("A voice agent for five clinics on Monday")
    b.update("appointment booking", "Monday", "technical lead")
    await b.availability(b.revision, 0)
    b.propose(b.revision, "Monday 10:00")


@pytest.mark.asyncio
async def test_confirm_and_retry():
    b = Booking()
    await offer(b)
    b.hear("confirm")
    assert b.confirm(b.revision, "Monday 10:00")["status"] == "booked"
    assert b.confirm(b.revision, "Monday 10:00")["status"] == "already_booked"
    assert b.confirm(b.revision, "Monday 14:00")["booking"]["slot"] == "Monday 10:00"


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["no", "yes but Friday", "don't book it", "Monday", ""])
async def test_no_inferred_confirmation(text):
    b = Booking()
    await offer(b)
    b.hear(text)
    assert b.confirm(b.revision, "Monday 10:00")["status"] == "rejected"
    assert b.booked is None


@pytest.mark.asyncio
async def test_confirmation_must_follow_proposal():
    b = Booking()
    b.hear("confirm")
    b.update("voice", "Monday", "CTO")
    await b.availability(b.revision, 0)
    b.propose(b.revision, "Monday 10:00")
    assert b.confirm(b.revision, "Monday 10:00")["status"] == "needs_confirmation"


@pytest.mark.asyncio
async def test_new_speech_invalidates_inflight_lookup():
    b = Booking()
    b.update("voice", "Friday", "CTO")
    task = asyncio.create_task(b.availability(b.revision, 0.01))
    await asyncio.sleep(0)
    b.hear("Actually Monday")
    assert (await task)["status"] == "superseded"
    assert b.offered == []


@pytest.mark.asyncio
async def test_correction_invalidates_offer():
    b = Booking()
    await offer(b)
    old = b.revision
    b.update("voice", "Friday", "CTO")
    b.hear("confirm")
    assert b.confirm(old, "Monday 10:00")["status"] == "rejected"


@pytest.mark.asyncio
async def test_cannot_book_other_offered_slot():
    b = Booking()
    await offer(b)
    b.hear("confirm")
    assert b.confirm(b.revision, "Monday 14:00")["status"] == "rejected"


@pytest.mark.asyncio
async def test_calls_are_isolated():
    a, b = Booking(), Booking()
    await offer(a)
    a.hear("confirm")
    a.confirm(a.revision, "Monday 10:00")
    assert b.booked is None and b.offered == []


@pytest.mark.asyncio
async def test_new_speech_after_lookup_needs_fresh_lookup():
    b = Booking()
    b.update("voice", "Friday", "CTO")
    await b.availability(b.revision, 0)
    b.hear("Actually Monday")
    assert b.propose(b.revision, "Friday 11:00")["status"] == "rejected"


@pytest.mark.asyncio
async def test_correction_then_yes_cannot_confirm_old_proposal():
    b = Booking()
    await offer(b)
    b.hear("Actually Friday")
    b.hear("confirm")
    assert b.confirm(b.revision, "Monday 10:00")["status"] == "rejected"
    assert b.booked is None


@pytest.mark.asyncio
async def test_retry_cannot_reuse_unreviewed_request():
    b = Booking()
    b.hear("Friday")
    b.update("voice", "Friday", "CTO")
    b.hear("Actually Monday")
    assert (await b.availability(b.revision, 0))["status"] == "superseded"
    b.update("voice", "Monday", "CTO")
    assert (await b.availability(b.revision, 0))["slots"] == ["Monday 10:00", "Monday 14:00"]


def test_slots_read_back_in_toronto_time():
    assert spoken("Friday 11:00") == "Friday at 11 a.m. Toronto time"
    assert spoken("Friday 15:00") == "Friday at 3 p.m. Toronto time"
    assert spoken("Monday 10:00") == "Monday at 10 a.m. Toronto time"


@pytest.mark.asyncio
async def test_day_correction_requeries_the_new_day():
    b = Booking()
    b.hear("Can we meet Monday morning?")
    b.update("dental booking agent", "monday", "CTO")
    lookup = asyncio.create_task(b.availability(b.revision, 0.01))
    await asyncio.sleep(0)
    b.hear("Actually, Friday works better than Monday.")
    result = b.update("dental booking agent", "friday", "CTO")
    # The correction itself returns Friday's slots; the Monday lookup is stale.
    assert result["status"] == "available"
    assert result["slots"] == ["Friday 11:00", "Friday 15:00"]
    assert result["read_back"] == [
        "Friday at 11 a.m. Toronto time",
        "Friday at 3 p.m. Toronto time",
    ]
    assert (await lookup)["status"] == "superseded"
    assert b.offered == ["Friday 11:00", "Friday 15:00"]
    proposal = b.propose(result["revision"], "Friday 11:00")
    assert proposal["status"] == "awaiting_confirmation"
    assert proposal["read_back"] == "Friday at 11 a.m. Toronto time"
    assert b.propose(result["revision"], "Monday 10:00")["status"] == "rejected"
    b.hear("Confirm.")
    assert b.confirm(result["revision"], "Friday 11:00")["booking"]["slot"] == "Friday 11:00"


def test_first_request_still_needs_a_lookup():
    b = Booking()
    b.hear("Friday please")
    result = b.update("voice", "friday", "CTO")
    assert "slots" not in result
    assert b.propose(b.revision, "Friday 11:00")["status"] == "rejected"
