---
title: How to build a clinic scheduling voice agent
summary: Build a clinic receptionist that finds open slots, books one appointment, and reschedules mid-call, from an empty folder to a running worker, on OpenAI.
author: Mahimai
github: mahimairaja
---

## What you will build

A voice receptionist for a clinic. The caller asks what is open, the agent reads
back real upcoming weekday slots, confirms a name and reason, books, and can
move the booking mid-call. The state is small: open slots plus a single active
booking. It lives in the agent process and mirrors to the playground as a List
of slots and a Card for the appointment. The finished demo is `clinic-scheduler`.

You need an OpenAI key and three LiveKit values (URL, API key, API secret). And
`uv`. The whole stack runs on the one OpenAI key.

## 1. Scaffold

Pin Python to `3.11`. `pyproject.toml` pulls the single OpenAI plugin:

```toml
[project]
name = "clinic-scheduler"
version = "0.1.0"
description = "Book a doctor appointment by voice: find an open slot, confirm it, and reschedule if plans change."
requires-python = ">=3.11"
dependencies = [
    "livekit-agents[openai]>=1.6,<2.0",
    "python-dotenv>=1.0",
]
```

`.env.example` lists only what this demo uses:

```
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
OPENAI_API_KEY=
```

## 2. A slot inventory that never goes stale

The slots are a template of `(business-day offset, time, doctor)` tuples, not
literal dates. The builder walks forward from today, skips weekends, and renders
the weekday label from a real date, so the spoken weekday always matches the
actual date:

```python
_SLOT_TEMPLATE = [
    (0, "9:00 AM", "Dr. Chen"),
    (0, "2:30 PM", "Dr. Patel"),
    (1, "10:00 AM", "Dr. Chen"),
    (1, "3:00 PM", "Dr. Patel"),
    (2, "11:30 AM", "Dr. Lee"),
    (3, "8:30 AM", "Dr. Chen"),
]


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

> [!WARNING]
> Call `_build_slots()` per session, not at module level. A module-level call
> computes the inventory once when the worker starts, and every caller for days
> hears the same frozen dates. The entrypoint rebuilds it off
> `datetime.date.today()` at the start of every call.

## 3. The four tools and the one invariant

`find_slots` filters the open list and publishes it. Pass a day or doctor to
narrow, omit both to show everything:

```python
@function_tool()
async def find_slots(
    self,
    context: RunContext[dict],
    date_preference: str | None = None,
    doctor: str | None = None,
) -> str:
    filtered = context.userdata["available_slots"]
    if date_preference:
        keyword = date_preference.lower()
        filtered = [s for s in filtered if keyword in s["date"].lower()]
    if doctor:
        name = doctor.lower()
        filtered = [s for s in filtered if name in s["doctor"].lower()]

    mounted = context.userdata["ui_mounted"]
    if not filtered:
        _publish_slots(self.room, mounted, [])
        return "No slots match that preference. Try a different day or doctor."

    _publish_slots(self.room, mounted, filtered)
    return f"Available slots: {_slots_summary(filtered)}"
```

The hard part is not booking, it is keeping one invariant true across every path
that mutates state: open slots plus the one booking must always stay a single
coherent set, with no slot lost and none double-counted. The model enforces
exactly one active booking, so `book_appointment` refuses a second one instead
of overwriting, then removes the taken slot from inventory:

```python
@function_tool()
async def book_appointment(
    self,
    context: RunContext[dict],
    slot_id: str,
    patient_name: str,
    reason: str,
) -> str:
    existing = context.userdata.get("booking")
    if existing is not None:
        return (
            f"You already have an appointment on {existing['date']} at "
            f"{existing['time']}. To change it, ask to reschedule."
        )

    available = context.userdata["available_slots"]
    slot = next((s for s in available if s["id"] == slot_id), None)
    if slot is None:
        return f"Slot {slot_id} is not available. Ask the caller to pick another."

    booking = {
        "slot_id": slot_id,
        "date": slot["date"],
        "time": slot["time"],
        "doctor": slot["doctor"],
        "patient": patient_name.strip() or "Unknown",
        "reason": reason.strip() or "general visit",
    }
    context.userdata["booking"] = booking
    context.userdata["available_slots"] = [s for s in available if s["id"] != slot_id]
    ...
```

That single decision (one active booking) makes the rest fall out. A move always
routes through `reschedule`, the one place that runs the free-old-take-new swap.
The freed slot goes back to inventory and re-sorts by its `s1..s6` id, so it
reappears in its natural position instead of drifting to the end:

```python
remaining = [s for s in available if s["id"] != new_slot_id]
remaining.append(_freed_slot(booking))
remaining.sort(key=lambda s: int(s["id"][1:]))
context.userdata["available_slots"] = remaining
```

`cancel_appointment` returns the slot without taking a new one and clears the
card. Reschedule is net-zero on inventory; a cancel returns exactly what the
booking took.

## 4. Mirror it on screen

Every mutation re-publishes the open List and the booking Card, so the screen
always matches state. A `_ui_action` helper mounts a component once then updates
it; cancel unmounts the Card so a gone booking does not linger:

```python
def _unmount_booking(room: rtc.Room, mounted: set[str]) -> None:
    if "booking" not in mounted:
        return
    mounted.discard("booking")
    publish_ui_event(room, "Card", "unmount", component_id="booking")
```

The open slots render as a who-is-free schedule: each row anchors on the doctor
with an initials avatar, the day as subtitle, and the time on the right.

## 5. The eval

The behavioral test runs the agent in text mode against the real `gpt-4o-mini`
and scores the conversation with LiveKit's `evals` judges. It asks what is open,
books the earliest, and asserts the task-completion, tool-use, and relevancy
judges pass:

```python
await session.run(user_input="What appointments are open this week?")
await session.run(user_input="Book me the earliest one. My name is Alex Reed.")

judges = JudgeGroup(
    llm=judge_llm,
    judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
)
evaluation = await judges.evaluate(session.history)
assert evaluation.all_passed
```

## 6. Run it

```sh
cp .env.example .env
```

```sh
uv sync
```

```sh
uv run python agent.py dev
```

Open https://playground.mahimai.ca/demos/clinic-scheduler and connect. Ask what
is open, book a slot, then try to book a second one: the agent refuses and
offers to move your existing appointment, because there is only ever one.
