"""Clinic scheduler voice agent.

Books a doctor appointment by voice: finds open slots, confirms a booking,
and handles reschedules.

Run it:
1. Copy templates/livekit-base/.env.example to .env and fill the keys.
2. Run: uv sync
3. Run: uv run --no-project python agent.py download-files
4. Run: uv run --no-project python agent.py dev
   Then open https://playground.mahimai.ca/demos/clinic-scheduler.
"""

import asyncio
import datetime
import json
import logging
from typing import Literal

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
)
from livekit.plugins import openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()

logger = logging.getLogger(__name__)

# Slots are generated on the next business days from today so the dates never
# go stale. Each spec is (business-day offset, time, doctor); the date label is
# computed from a real upcoming weekday, so the weekday always matches the date.
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


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
    component_id: str | None = None,
) -> None:
    envelope = {
        "type": "ui_event",
        "component": component,
        "action": action,
        "props": props or {},
    }
    if component_id is not None:
        envelope["id"] = component_id

    try:
        payload = json.dumps(envelope).encode("utf-8")
    except (TypeError, ValueError):
        logger.exception("failed to encode playground ui event")
        return

    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(
                payload,
                topic="ui",
                reliable=True,
            )
        )
    except RuntimeError:
        logger.exception("failed to schedule playground ui event")
        return

    def log_publish_failure(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("failed to publish playground ui event")

    task.add_done_callback(log_publish_failure)


def _ui_action(mounted: set[str], component_id: str) -> Literal["mount", "update"]:
    if component_id in mounted:
        return "update"
    mounted.add(component_id)
    return "mount"


def _slots_summary(slots: list[dict]) -> str:
    return "; ".join(
        f"{s['id']}: {s['date']} at {s['time']} with {s['doctor']}" for s in slots
    )


def _freed_slot(booking: dict) -> dict:
    """Rebuild an open-slot record from a booking, to return it to inventory."""
    return {
        "id": booking["slot_id"],
        "date": booking["date"],
        "time": booking["time"],
        "doctor": booking["doctor"],
    }


def _publish_slots(room: rtc.Room, mounted: set[str], slots: list[dict]) -> None:
    publish_ui_event(
        room,
        "List",
        _ui_action(mounted, "slots"),
        component_id="slots",
        props={
            "title": "open slots",
            "items": [
                {
                    "title": s["date"],
                    "subtitle": s["doctor"],
                    "right": s["time"],
                }
                for s in slots
            ],
        },
    )


def _publish_booking(
    room: rtc.Room, mounted: set[str], booking: dict, *, rescheduled: bool = False
) -> None:
    footer = "rescheduled" if rescheduled else "confirmed"
    publish_ui_event(
        room,
        "Card",
        _ui_action(mounted, "booking"),
        component_id="booking",
        props={
            "title": f"{booking['date']} at {booking['time']}",
            "subtitle": booking["doctor"],
            "body": f"Patient: {booking['patient']}\nReason: {booking['reason']}",
            "footer": footer,
            "accent": True,
        },
    )


def _unmount_booking(room: rtc.Room, mounted: set[str]) -> None:
    """Clear the booking Card after a cancel so the screen matches state.

    Only unmounts when the card is up, and forgets the id so a later booking
    mounts fresh instead of updating a card that is gone.
    """
    if "booking" not in mounted:
        return
    mounted.discard("booking")
    publish_ui_event(room, "Card", "unmount", component_id="booking")


class ClinicScheduler(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                "You are a friendly clinic receptionist scheduling appointments by phone. "
                "Ask what day or doctor the caller prefers, then call find_slots to show options. "
                "Read the options naturally, confirm the patient name and reason, then call book_appointment. "
                "If they want to change their booking, call find_slots again, then call reschedule. "
                "If they want to cancel and not rebook, call cancel_appointment. "
                "Keep replies short, plain text, no markdown or emojis."
            ),
        )
        self.room = room

    @function_tool()
    async def find_slots(
        self,
        context: RunContext[dict],
        date_preference: str | None = None,
        doctor: str | None = None,
    ) -> str:
        """Return available appointment slots, optionally filtered.

        Call this when the caller asks what times are open. Pass date_preference
        as a partial match like 'Monday' or 'June 10', and/or doctor as a partial
        name like 'Chen'. Omit both to show all slots.
        """
        filtered = context.userdata["available_slots"]
        if date_preference:
            keyword = date_preference.lower()
            filtered = [s for s in filtered if keyword in s["date"].lower()]
        if doctor:
            name = doctor.lower()
            filtered = [s for s in filtered if name in s["doctor"].lower()]

        mounted = context.userdata["ui_mounted"]
        if not filtered:
            # Clear the list so the screen does not keep showing stale rows.
            _publish_slots(self.room, mounted, [])
            return "No slots match that preference. Try a different day or doctor."

        _publish_slots(self.room, mounted, filtered)
        return f"Available slots: {_slots_summary(filtered)}"

    @function_tool()
    async def book_appointment(
        self,
        context: RunContext[dict],
        slot_id: str,
        patient_name: str,
        reason: str,
    ) -> str:
        """Book an appointment for the caller.

        Pass the slot_id from find_slots, the patient full name, and a brief
        reason for the visit. Call after confirming name and reason with the caller.
        """
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
        context.userdata["available_slots"] = [
            s for s in available if s["id"] != slot_id
        ]

        mounted = context.userdata["ui_mounted"]
        _publish_slots(self.room, mounted, context.userdata["available_slots"])
        _publish_booking(self.room, mounted, booking)
        return (
            f"Booked. {booking['patient']} sees {booking['doctor']} on "
            f"{booking['date']} at {booking['time']} for {booking['reason']}."
        )

    @function_tool()
    async def reschedule(
        self,
        context: RunContext[dict],
        new_slot_id: str,
    ) -> str:
        """Move the existing booking to a different slot.

        The caller must already have a booking. Call find_slots first if
        they need to see new options before picking.
        """
        booking = context.userdata.get("booking")
        if booking is None:
            return "No booking to reschedule. Book an appointment first."

        if new_slot_id == booking["slot_id"]:
            return (
                f"You are already booked into that slot on {booking['date']} "
                f"at {booking['time']}. Pick a different one to move."
            )

        available = context.userdata["available_slots"]
        new_slot = next((s for s in available if s["id"] == new_slot_id), None)
        if new_slot is None:
            return f"Slot {new_slot_id} is not available. Try another slot."

        # Return the freed slot to inventory, then re-sort by the original
        # s1..s6 id so a freed early slot reappears in its natural position
        # instead of drifting to the end after a reschedule.
        remaining = [s for s in available if s["id"] != new_slot_id]
        remaining.append(_freed_slot(booking))
        remaining.sort(key=lambda s: int(s["id"][1:]))
        context.userdata["available_slots"] = remaining

        booking.update(
            {
                "slot_id": new_slot_id,
                "date": new_slot["date"],
                "time": new_slot["time"],
                "doctor": new_slot["doctor"],
            }
        )
        mounted = context.userdata["ui_mounted"]
        _publish_slots(self.room, mounted, context.userdata["available_slots"])
        _publish_booking(self.room, mounted, booking, rescheduled=True)
        return (
            f"Rescheduled. {booking['patient']} now sees {booking['doctor']} on "
            f"{booking['date']} at {booking['time']}."
        )

    @function_tool()
    async def cancel_appointment(self, context: RunContext[dict]) -> str:
        """Cancel the caller's appointment and return its slot to the open list.

        Call this when the caller wants to cancel and does not want to rebook.
        """
        booking = context.userdata.get("booking")
        if booking is None:
            return "There is no appointment booked to cancel."

        # Return the freed slot to inventory in its natural s1..s6 position.
        available = context.userdata["available_slots"]
        available.append(_freed_slot(booking))
        available.sort(key=lambda s: int(s["id"][1:]))
        context.userdata["available_slots"] = available
        context.userdata["booking"] = None

        mounted = context.userdata["ui_mounted"]
        _publish_slots(self.room, mounted, available)
        _unmount_booking(self.room, mounted)
        return (
            f"Cancelled {booking['patient']}'s appointment on {booking['date']} "
            f"at {booking['time']}. That slot is open again."
        )


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="clinic-scheduler")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    # Build the slot inventory per session so every new call recomputes the
    # upcoming weekdays from today's date, not from when the worker started.
    userdata: dict = {
        "available_slots": _build_slots(),
        "booking": None,
        "ui_mounted": set(),
    }
    session = AgentSession(
        userdata=userdata,
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(voice="shimmer"),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )

    await session.start(agent=ClinicScheduler(ctx.room), room=ctx.room)
    await ctx.connect()
    await session.generate_reply(
        instructions="Greet the caller and ask how you can help them schedule an appointment today."
    )


if __name__ == "__main__":
    cli.run_app(server)
