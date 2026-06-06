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

SLOTS = [
    {"id": "s1", "date": "Monday June 9", "time": "9:00 AM", "doctor": "Dr. Chen"},
    {"id": "s2", "date": "Monday June 9", "time": "2:30 PM", "doctor": "Dr. Patel"},
    {"id": "s3", "date": "Tuesday June 10", "time": "10:00 AM", "doctor": "Dr. Chen"},
    {"id": "s4", "date": "Tuesday June 10", "time": "3:00 PM", "doctor": "Dr. Patel"},
    {"id": "s5", "date": "Wednesday June 11", "time": "11:30 AM", "doctor": "Dr. Lee"},
    {"id": "s6", "date": "Thursday June 12", "time": "8:30 AM", "doctor": "Dr. Chen"},
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


def _ui_action(room: rtc.Room, component_id: str) -> Literal["mount", "update"]:
    mounted = getattr(room, "_awesome_voice_ui_mounted", None)
    if mounted is None:
        mounted = set()
        setattr(room, "_awesome_voice_ui_mounted", mounted)
    if component_id in mounted:
        return "update"
    mounted.add(component_id)
    return "mount"


def _slots_summary(slots: list[dict]) -> str:
    return "; ".join(
        f"{s['id']}: {s['date']} at {s['time']} with {s['doctor']}"
        for s in slots
    )


def _publish_slots(room: rtc.Room, slots: list[dict]) -> None:
    publish_ui_event(
        room,
        "List",
        _ui_action(room, "slots"),
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


def _publish_booking(room: rtc.Room, booking: dict, *, rescheduled: bool = False) -> None:
    footer = "rescheduled" if rescheduled else "confirmed"
    publish_ui_event(
        room,
        "Card",
        _ui_action(room, "booking"),
        component_id="booking",
        props={
            "title": f"{booking['date']} at {booking['time']}",
            "subtitle": booking["doctor"],
            "body": f"Patient: {booking['patient']}\nReason: {booking['reason']}",
            "footer": footer,
            "accent": True,
        },
    )


class ClinicScheduler(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                "You are a friendly clinic receptionist scheduling appointments by phone. "
                "Ask what day or doctor the caller prefers, then call find_slots to show options. "
                "Read the options naturally, confirm the patient name and reason, then call book_appointment. "
                "If they want to change their booking, call find_slots again, then call reschedule. "
                "Keep replies short, plain text, no markdown or emojis."
            ),
        )
        self.room = room

    @function_tool()
    async def find_slots(
        self,
        context: RunContext[dict],
        date_preference: str | None = None,
    ) -> str:
        """Return available appointment slots, optionally filtered by a date keyword.

        Call this when the caller asks what times are open. Pass date_preference
        as a partial match like 'Monday' or 'June 10'. Omit to show all slots.
        """
        available = context.userdata["available_slots"]
        if date_preference:
            keyword = date_preference.lower()
            filtered = [
                s for s in available
                if keyword in s["date"].lower() or keyword in s["time"].lower()
            ]
        else:
            filtered = available

        if not filtered:
            return "No slots match that preference. Try a different day."

        _publish_slots(self.room, filtered)
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

        _publish_booking(self.room, booking)
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

        available = context.userdata["available_slots"]
        new_slot = next((s for s in available if s["id"] == new_slot_id), None)
        if new_slot is None:
            return f"Slot {new_slot_id} is not available. Try another slot."

        freed = {
            "id": booking["slot_id"],
            "date": booking["date"],
            "time": booking["time"],
            "doctor": booking["doctor"],
        }
        context.userdata["available_slots"] = [
            s for s in available if s["id"] != new_slot_id
        ] + [freed]

        booking.update({
            "slot_id": new_slot_id,
            "date": new_slot["date"],
            "time": new_slot["time"],
            "doctor": new_slot["doctor"],
        })
        _publish_booking(self.room, booking, rescheduled=True)
        return (
            f"Rescheduled. {booking['patient']} now sees {booking['doctor']} on "
            f"{booking['date']} at {booking['time']}."
        )


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="clinic-scheduler")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    userdata: dict = {
        "available_slots": list(SLOTS),
        "booking": None,
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
