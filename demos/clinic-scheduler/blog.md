---
title: A clinic receptionist that books, confirms, and reschedules by voice
summary: A voice receptionist that reads back real upcoming weekday slots, books one appointment, and moves it mid-call, plus the inventory invariant that took two review passes to settle.
author: Mahimai
---

## The problem

A clinic front desk spends its day on the phone booking, confirming, and
rescheduling appointments: high volume, low variation, and it ties up
reception staff. This demo is a voice receptionist for clinics and GP
offices that want to automate booking without a phone tree. The caller
asks what is open, the agent reads back real upcoming weekday slots,
confirms a name and reason, books, and can move the booking mid-call.
State (open slots plus the single active booking) lives in the agent
process and mirrors to the playground as a List of slots and a Card for
the confirmed appointment.

## Why this stack

The whole stack runs on one OpenAI key. Whisper handles STT, gpt-4o-mini
drives the three function tools (find_slots, book_appointment,
reschedule), and OpenAI TTS speaks back in the shimmer voice for the
receptionist persona. That replaces the template's Deepgram Nova-3 and
Cartesia Sonic-2 so the credential list stays at one provider. Silero VAD
loads once in prewarm and is reused per session, and the LiveKit
MultilingualModel turn detector ships unchanged from the template so the
agent knows when the caller has finished a turn.

## The interesting part

The slot data is a template of (business-day offset, time, doctor)
tuples, not literal dates. The builder walks forward from today, skips
weekends, and renders the weekday label from a real date, so the spoken
weekday always matches the date:

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

The original code hard-coded "Monday June 9" style literals that went
stale. This replaced them. The catch: a module-import call would compute
the inventory once when the worker started, so the entrypoint calls
_build_slots() per session instead, off datetime.date.today() at the
start of every call.

## What surprised me

Preserving the slot-inventory invariant across every mutation path took
two review passes. The first fix made reschedule free the old slot before
taking the new one and had book_appointment free a prior booking's slot on
re-book. The second pass changed the model: book_appointment now refuses a
second booking and tells the caller to reschedule, which made the
prior-slot-freeing branch dead code and removed it. The tradeoff that
emerged: rather than support overwrite-and-free semantics in two tools,
the demo enforces exactly one active booking and routes every change
through reschedule, the single place that runs the free-old-take-new swap.
Open slots plus booking stay one coherent set where reschedule is net-zero
on inventory. The same passes caught two over-matching bugs: find_slots
matched the keyword against date and time (so a bare number over-matched)
before it narrowed to date only, and it advertised a doctor filter the
prompt promised but the code ignored until the filter was wired in.

## Run it

Talk to it at https://playground.mahimai.ca/demos/clinic-scheduler. Ask
what is open, book a slot, then reschedule it mid-call. Or fork the
cookbook and run the worker locally.
