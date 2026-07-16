---
title: How to build a clinic scheduling voice agent
summary: A clinic receptionist that books, confirms, and reschedules doctor appointments by voice, on OpenAI.
author: Mahimai
github: mahimairaja
---

A clinic phone line does one thing all day: find an open slot, confirm it with
the caller, and move it when plans change. This agent does exactly that, holding
the slot inventory in memory and mirroring it on screen.

The stack is OpenAI for all three roles: STT, `gpt-4o-mini`, and TTS on the
shimmer voice. Turn-taking is LiveKit's inference VAD and turn detector.

The slots are generated from the next weekdays, computed from today, so the
dates never go stale:

```python
def _build_slots() -> list[dict]:
    days: list[datetime.date] = []
    day = datetime.date.today()
    while len(days) < 4:
        day += datetime.timedelta(days=1)
        if day.weekday() < 5:  # Monday-Friday
            days.append(day)
    ...
```

When the caller asks what is open, `find_slots` filters that inventory by an
optional day or doctor and publishes the matches as a List on screen. Omit both
filters and they see everything; a filter that matches nothing publishes an
empty List rather than leaving stale rows up.

The interesting part is the state. One booking at a time: `book_appointment`
refuses a second one instead of overwriting, and points the caller at
reschedule.

```python
existing = context.userdata.get("booking")
if existing is not None:
    return (
        f"You already have an appointment on {existing['date']} at "
        f"{existing['time']}. To change it, ask to reschedule."
    )
```

Booking moves the slot out of the open list; reschedule and cancel put the freed
slot back. The put-back re-sorts by the `s1..s6` id, so a freed early slot
reappears in its natural position instead of drifting to the end:

```python
remaining = [s for s in available if s["id"] != new_slot_id]
remaining.append(_freed_slot(booking))
remaining.sort(key=lambda s: int(s["id"][1:]))
```

> [!TIP]
> Every mutation re-publishes the List, and cancel unmounts the booking Card, so
> the screen always matches the inventory. A list that disagrees with state is
> the most visible bug.

Build it from an empty folder in the full walkthrough, or talk to the finished
agent at https://playground.mahimai.ca/demos/clinic-scheduler.
