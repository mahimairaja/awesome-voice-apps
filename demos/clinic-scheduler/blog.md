---
title: A clinic receptionist that books, confirms, and reschedules by voice
summary: A voice receptionist that reads back real upcoming weekday slots, books one appointment, and moves it mid-call. Plus the slot-inventory invariant that took two review passes to settle, and why the agent ended up enforcing exactly one active booking.
author: Mahimai
---

## The problem

A clinic front desk spends its day on the phone booking, confirming, and rescheduling appointments. High volume, low variation, and it ties up reception staff all day. This demo is a voice receptionist for clinics and GP offices that want to automate that booking flow without a phone tree.

The caller asks what is open, the agent reads back real upcoming weekday slots, confirms a name and reason, books, and can move the booking mid-call. The state is small: open slots plus a single active booking. It lives in the agent process and mirrors to the playground as a list of slots and a card for the confirmed appointment.

## Why this stack

The whole stack runs on one OpenAI key. Whisper handles STT, gpt-4o-mini drives the four function tools (`find_slots`, `book_appointment`, `reschedule`, `cancel_appointment`), and OpenAI TTS speaks back in the shimmer voice for the receptionist persona. That replaces the template's Deepgram Nova-3 and Cartesia Sonic-2, so the whole credential list collapses to one provider, which is the right trade for a demo someone wants to try in two minutes.

Silero VAD loads once in prewarm and is reused per session, and the LiveKit MultilingualModel turn detector ships unchanged from the template so the agent knows when the caller has finished a turn. Neither needed touching; the interesting work is entirely in the tools.

## The interesting part

The slot data is a template of (business-day offset, time, doctor) tuples, not literal dates. The builder walks forward from today, skips weekends, and renders the weekday label from a real date, so the spoken weekday always matches the actual date:

```python
def _build_slots() -> list[dict]:
    days: list[datetime.date] = []
    day = datetime.date.today()
    while len(days) < 4:
        day += datetime.timedelta(days=1)
        if day.weekday() < 5:  # Monday-Friday
            days.append(day)
    return [
        {
            "id": f"s{i + 1}",
            "date": f"{days[offset].strftime('%A %B')} {days[offset].day}",
            "time": time,
            "doctor": doctor,
        }
        for i, (offset, time, doctor) in enumerate(_SLOT_TEMPLATE)
    ]
```

The original code hard-coded "Monday June 9" style literals, which went stale the moment the date passed. This replaces them. The catch is where you call it: a module-level call computes the inventory once when the worker starts, and then every caller for days hears the same frozen dates. So the entrypoint calls `_build_slots()` per session instead, off `datetime.date.today()` at the start of every call.

![The slot inventory invariant](https://assets.mahimai.ca/clinic-scheduler-invariant-blog.svg)

## What surprised me

The hard part was not booking. It was keeping one invariant true across every path that mutates state: open slots plus the one booking must always stay a single coherent set, with no slot lost and none double-counted. It took two review passes to get there.

The first pass tried to make every tool clean up after itself: `reschedule` freed the old slot before taking the new one, and `book_appointment` freed a prior booking's slot when someone re-booked. It worked, but it meant two different tools both knew how to free-and-take, which is two places for the invariant to break.

The second pass changed the model instead of patching it. `book_appointment` now refuses a second booking outright and tells the caller to reschedule. That single decision deleted the prior-slot-freeing branch as dead code and collapsed the whole thing: the demo enforces exactly one active booking, a move always routes through `reschedule` (the one place that runs the free-old-take-new swap), and `cancel_appointment` frees the slot back without taking a new one and clears the card. Reschedule is net-zero on inventory; a cancel returns exactly what the booking took.

The same two passes caught a pair of over-matching bugs worth naming. `find_slots` matched the caller's keyword against both date and time, so a bare number like "two" over-matched a 2 p.m. slot before it was narrowed to date only. And the prompt advertised a doctor filter the code quietly ignored until the filter was actually wired in. Both are the kind of bug that only shows up when you read the tool against the prompt line by line.

## Run it

Talk to it at [playground.mahimai.ca/demos/clinic-scheduler](https://playground.mahimai.ca/demos/clinic-scheduler): ask what is open, book a slot, then reschedule it mid-call and watch the card update. Or fork the cookbook and run the worker locally.

To see the invariant hold, book a slot and then try to book a second one. The agent will refuse and offer to move your existing appointment instead, because there is only ever one.
