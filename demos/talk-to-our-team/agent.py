"""An inbound SDR simulation using GPT-Live, not the turn-based Realtime API."""

import asyncio
import json
import logging
import os
from typing import Literal

import voicegateway
from booking import Booking
from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, function_tool
from livekit.plugins import openai, silero

load_dotenv()
logger = logging.getLogger("sdr")

VOICE_INSTRUCTIONS = """You welcome visitors to a voice AI engineering studio.
This is a demonstration with a simulated calendar, not a real sales appointment.
Keep responses brief and warm. Ask one question at a time. Discover their use case,
scale, and timing without asking for private contact information. We help engineering
teams build voice agents, review production readiness, and improve existing systems.
Delegate questions requiring service fit, availability, or booking. Answer greetings
and general questions yourself. Acknowledge slow work once; do not narrate every step.
Accept interruptions and corrections. The latest requested day replaces the previous day.
Do not ask the visitor to reconfirm a day they just corrected. After delegating,
acknowledge the latest preference once and wait quietly for the current result.
A superseded result is internal: never ask the visitor to repeat because an old
lookup was superseded. When the backend proposes a slot, read the day, time and
Toronto time zone, then explicitly ask them to say confirm before booking. Never announce a booking until the backend
reports success. Explain that all slots are illustrative and in Toronto time.
"""
BACKEND_INSTRUCTIONS = """Handle service matching and simulated meetings.
Use update_request to record the latest need, preferred day, and attendee roles before
checking availability. This demo supports Monday and Friday only. Do not invent dates,
prices, guarantees, slots, or real bookings. Every corrected request needs update_request
and a new lookup. A superseded lookup must never be presented as current. When superseded, examine
the latest caller transcript, update the request and repeat the lookup without
asking the caller to repeat. Normalize Monday morning to day=monday and choose
its morning slot. After an available result, immediately propose a matching slot.
Use propose_meeting with one available slot, then have the voice agent read that exact
slot and time zone and ask the visitor to say 'confirm'. Only after a subsequent
confirmation use book_meeting with that revision and slot. Never call propose_meeting
and book_meeting in the same turn. Rejected tools are not successful actions.
Do not rebook or modify an existing simulated booking. Return concise verified facts.
"""


class SalesAssistant(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=VOICE_INSTRUCTIONS)
        self.booking = Booking()
        self._caller_turn = asyncio.Event()

    def hear(self, text: str) -> None:
        self.booking.hear(text)
        self._caller_turn.set()

    async def on_enter(self) -> None:
        # Appends steer the voice frontend; they are not transcript messages.
        self.duplex_session.append_instructions(
            "Never request an email or phone number in this public demonstration."
        )
        self.duplex_session.append_thinking(
            "The visitor opened the Talk to Our Team demo. All appointments are simulated."
        )
        handle = self.session.generate_reply(
            instructions="Say this is a simulated sales conversation and ask what they want to build."
        )
        await handle
        if handle.exception() is not None:
            logger.info("Greeting was not started; waiting for visitor speech")

    @function_tool
    async def update_request(
        self, need: str, day: Literal["monday", "friday"], attendees: str
    ) -> str:
        """Save the latest use case, preferred weekday, and attendee roles, including corrections."""
        return json.dumps(self.booking.update(need, day, attendees))

    @function_tool
    async def check_availability(self, revision: int) -> str:
        """Look up simulated slots for the current request revision; may return superseded."""
        delay = min(5.0, max(0.0, float(os.getenv("SDR_LOOKUP_DELAY", "2"))))
        result = await self.booking.availability(revision, delay)
        if result["status"] == "available":
            self.duplex_session.append_thinking(
                f"Current calendar lookup is for {self.booking.day}. "
                "No meeting has been booked. Wait for the backend's proposed slot."
            )
        return json.dumps(result)

    @function_tool
    async def propose_meeting(self, revision: int, slot: str) -> str:
        """Select one returned slot and request a new explicit caller confirmation before booking."""
        result = self.booking.propose(revision, slot)
        if result["status"] == "awaiting_confirmation":
            self._caller_turn.clear()
        return json.dumps(result)

    @function_tool
    async def book_meeting(self, revision: int, slot: str) -> str:
        """Book only a previously proposed slot after the caller's fresh standalone confirmation."""
        # A delegated tool can arrive before LiveKit finalizes the caller's
        # transcript. Wait for that event, never infer consent from tool arguments.
        if self.booking.proposed and self.booking.user_turn <= self.booking.offered_turn:
            try:
                await asyncio.wait_for(self._caller_turn.wait(), timeout=3)
            except TimeoutError:
                pass
        result = self.booking.confirm(revision, slot)
        # The backend returns the verified outcome to the voice model. Sending it
        # again as commentary here would risk announcing the same result twice.
        return json.dumps(result)

    @function_tool
    async def explain_services(self, need: str) -> str:
        """Return the studio's service catalogue to help qualify a visitor's engineering need."""
        return json.dumps(
            {
                "services": [
                    "Prototype to production",
                    "Production readiness review",
                    "Embedded voice engineering",
                    "Custom speech models",
                ],
                "pricing": "Scoped after a discussion; no quote in this simulation.",
                "visitor_need": need,
            }
        )


server = AgentServer()


@server.rtc_session(agent_name="talk-to-our-team")
async def entrypoint(ctx: JobContext) -> None:
    assistant = SalesAssistant()
    session = AgentSession(
        llm=openai.realtime.GPTLiveModel(
            voice="marin",
            responses_options={
                "model": os.getenv("SDR_BACKEND_MODEL", "gpt-5.6-luna"),
                "instructions": BACKEND_INSTRUCTIONS,
                "parallel_tool_calls": False,
                "max_output_tokens": 600,
                "reasoning": {"effort": "low"},
            },
        ),
        vad=silero.VAD.load(),
    )
    voicegateway.attach(
        session,
        project="gpt-live-sdr",
        agent_id="talk-to-our-team",
        transcript=False,
        snapshots=False,
        turns=False,
        dead_air=False,
    )

    @session.on("user_input_transcribed")
    def heard(event):
        if event.is_final:
            assistant.hear(event.transcript)

    async def final_usage():
        logger.info("Usage after close: %s", session.usage)

    ctx.add_shutdown_callback(final_usage)
    await session.start(agent=assistant, room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
