"""postop-checkin: a day-3 check-in call after a knee replacement.

The agent asks the protocol's questions (breathing, calf, temperature,
incision, pain) and records each answer through a typed tool. A fixed rule
table in protocol.py decides the escalation: chest pain stops the call with a
911 script, a fever or a spreading redness goes to the nurse line with an SBAR
handoff note, and everything else is routine. The model extracts; code decides.

Stack: Deepgram Nova-3 STT, OpenAI gpt-4o-mini, Cartesia Sonic 3 TTS.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
import json
import logging

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
from livekit.plugins import cartesia, deepgram, openai, silero

import protocol
from protocol import Drainage, TempUnit

load_dotenv()

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    f"You make the day-3 check-in call for the orthopedic team at {protocol.HOSPITAL} "
    "after a total knee replacement. You follow a fixed protocol. Ask one question at a "
    "time, in the order the tools give you. When the patient answers, call the matching "
    "record tool with what they said; if they answer several questions at once, record "
    "each. Pass patient_words as a short quote of their own words. Temperature readings "
    "are numbers like 101.8 or 38.2; if they have no thermometer, pass no reading and ask "
    "about chills or sweats. Wound and pain questions have several parts: ask for the "
    "missing part before recording. You never judge whether something is serious, never "
    "reassure, and never give medical advice: the tools decide, and you say what they "
    "tell you to say. If a tool says EMERGENCY, say its script word for word and ask "
    "nothing else. If the patient asks for advice, say the nurse can help with that. "
    "When every question is answered, call finish_check_in. Keep replies short, plain "
    "text, no markdown, no emojis."
)


def make_stt() -> deepgram.STT:
    # Smart format writes "one oh one point eight" as 101.8 for the temperature rule.
    return deepgram.STT(model="nova-3", smart_format=True)


def publish_ui_event(room: rtc.Room, component: str, props: dict) -> None:
    envelope = {"type": "ui_event", "component": component, "action": "update", "props": props}
    payload = json.dumps(envelope).encode("utf-8")
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except RuntimeError:
        logger.exception("failed to schedule playground ui event")
        return
    task.add_done_callback(lambda t: t.cancelled() or t.exception())


def publish_checkin(room: rtc.Room, state: dict) -> None:
    publish_ui_event(room, "PostOp", protocol.snapshot(state))


class CheckInCall(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room

    def _record(self, context: RunContext[dict], step: str, value: dict, words: str) -> str:
        result = protocol.record(context.userdata, step, value, words)
        logger.info("recorded %s: %s", step, result.split(".")[0])
        publish_checkin(self.room, context.userdata)
        return result

    @function_tool()
    async def record_breathing(
        self, context: RunContext[dict], chest_pain_or_short_of_breath: bool, patient_words: str
    ) -> str:
        """Record whether the patient has had any chest pain or shortness of breath."""
        value = {"chest_pain_or_short_of_breath": chest_pain_or_short_of_breath}
        return self._record(context, "breathing", value, patient_words)

    @function_tool()
    async def record_calf(
        self, context: RunContext[dict], calf_pain_or_swelling: bool, patient_words: str
    ) -> str:
        """Record whether either calf has new pain, swelling or tenderness."""
        value = {"calf_pain_or_swelling": calf_pain_or_swelling}
        return self._record(context, "calf", value, patient_words)

    @function_tool()
    async def record_temperature(
        self,
        context: RunContext[dict],
        reading: float | None,
        unit: TempUnit,
        chills_or_sweats: bool,
        patient_words: str,
    ) -> str:
        """Record the temperature reading, or null when they have none.

        unit is F or C when the patient says it, otherwise unknown.
        """
        value = {"reading": reading, "unit": unit, "chills_or_sweats": chills_or_sweats}
        return self._record(context, "temperature", value, patient_words)

    @function_tool()
    async def record_wound(
        self,
        context: RunContext[dict],
        redness_spreading: bool,
        drainage: Drainage,
        edges_opening: bool,
        patient_words: str,
    ) -> str:
        """Record how the incision looks.

        drainage: none, clear, spotting (a little blood), or cloudy_or_pus.
        """
        value = {
            "redness_spreading": redness_spreading,
            "drainage": drainage,
            "edges_opening": edges_opening,
        }
        return self._record(context, "wound", value, patient_words)

    @function_tool()
    async def record_pain(
        self,
        context: RunContext[dict],
        score: int,
        medication_helping: bool,
        patient_words: str,
    ) -> str:
        """Record the pain score from 0 to 10 and whether the medication helps."""
        value = {"score": score, "medication_helping": medication_helping}
        return self._record(context, "pain", value, patient_words)

    @function_tool()
    async def finish_check_in(self, context: RunContext[dict]) -> str:
        """Close the check-in once every question is answered. Returns what to say."""
        result = protocol.finish(context.userdata)
        publish_checkin(self.room, context.userdata)
        return result

    @function_tool()
    async def start_over(self, context: RunContext[dict]) -> str:
        """Run the check-in again with new answers. Only after it has finished."""
        result = protocol.start_over(context.userdata)
        publish_checkin(self.room, context.userdata)
        return result


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="postop-checkin")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        userdata=protocol.initial_state(),
        stt=make_stt(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
        vad=ctx.proc.userdata["vad"],
    )
    await session.start(agent=CheckInCall(ctx.room), room=ctx.room)
    await ctx.connect()
    publish_checkin(ctx.room, session.userdata)
    patient = session.userdata["patient"]
    await session.generate_reply(
        instructions=(
            f"Say you are calling from {protocol.HOSPITAL} orthopedics for "
            f"{patient['name']}'s day-3 check-in after their knee replacement, then ask: "
            f"{protocol.STEPS[0]['ask']}"
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
