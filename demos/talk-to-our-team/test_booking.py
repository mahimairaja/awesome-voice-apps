import asyncio

import pytest
from booking import Booking


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
