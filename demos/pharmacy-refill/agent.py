"""pharmacy-refill: a prescription refill line that gets codes exactly right.

The caller refills the prescription on the bottle label shown on screen: drug
name, Rx number, date of birth, postal code and pickup store. Every value is
read back and confirmed before it counts, sound-alike drugs are caught,
codes are spoken character by character, and the call log is redacted.

Stack: Deepgram Nova-3 STT with drug-name keyterms, OpenAI gpt-4o-mini, Cartesia
Sonic 2 TTS.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterable

import refill
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    ModelSettings,
    RunContext,
    cli,
    function_tool,
)
from livekit.plugins import cartesia, deepgram, openai, silero
from refill import FieldName

load_dotenv()

logger = logging.getLogger(__name__)

# Nova-3 keyterm prompting: bias recognition toward the formulary and brand.
KEYTERMS = [*refill.FORMULARY, "Larkfield", "Danforth"]

INSTRUCTIONS = (
    f"You answer the prescription refill line at {refill.PHARMACY}. Collect, one at a "
    "time: the medication name, the Rx number, the caller's date of birth, their postal "
    "code, and which store they will pick up from. The caller reads these from the "
    "bottle label on their screen. For every answer call capture with the exact field "
    "name. Pass rx_number and postal_code as the characters you heard, like 4471-B or "
    "M5V 2T6, and date_of_birth as YYYY-MM-DD. If the caller gives several details at "
    "once, capture each. After a capture, read the value back exactly as the tool says "
    "and ask if it is right. Only when the caller says yes, call confirm for that field. "
    "If they correct you, apologise in a few words and capture the corrected value. "
    "If a tool rejects a value or reports a mismatch, say why in one sentence and ask "
    "again. Never guess a value and never reveal what is on file. When every field is "
    "confirmed, call place_refill and tell them the reference and pickup time. Keep "
    "replies short, plain text, no markdown, no emojis."
)


def make_stt() -> deepgram.STT:
    return deepgram.STT(model="nova-3", keyterm=KEYTERMS, smart_format=True)


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


def publish_refill(room: rtc.Room, state: dict) -> None:
    publish_ui_event(room, "Refill", refill.snapshot(state))


class RefillLine(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room

    @function_tool()
    async def capture(self, context: RunContext[dict], field: FieldName, value: str) -> str:
        """Record what the caller said for one field, before they confirm it.

        Use the exact field name. rx_number and postal_code as characters,
        date_of_birth as YYYY-MM-DD.
        """
        result = refill.capture(context.userdata, field, value)
        publish_refill(self.room, context.userdata)
        return result

    @function_tool()
    async def confirm(self, context: RunContext[dict], field: FieldName) -> str:
        """The caller said yes to your read-back of this field."""
        result = refill.confirm(context.userdata, field)
        publish_refill(self.room, context.userdata)
        return result

    @function_tool()
    async def place_refill(self, context: RunContext[dict]) -> str:
        """Place the refill. Refuses until every field is confirmed."""
        result = refill.place_refill(context.userdata)
        publish_refill(self.room, context.userdata)
        return result

    def log_line(self, who: str, text: str) -> None:
        """Redact a transcript line before it is logged or shown."""
        clean, tags = refill.redact(text)
        if not clean:
            return
        state = self.session.userdata
        state["log"] = [*state["log"], {"who": who, "text": clean[:240], "tags": tags}][-6:]
        logger.debug("%s: %s", who, clean)
        publish_refill(self.room, state)

    def watch_transcript(self) -> None:
        def added(event) -> None:
            item = event.item
            role = getattr(item, "role", None)
            text = getattr(item, "text_content", None)
            if role in ("user", "assistant") and text:
                self.log_line("caller" if role == "user" else "agent", text)

        self.session.on("conversation_item_added", added)

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        async def spoken() -> AsyncIterable[str]:
            # Codes can be split across LLM chunks: rewrite whole words only.
            buffer = ""
            async for chunk in text:
                ready, buffer = refill.split_ready(buffer + chunk)
                if ready:
                    yield self.speak(ready)
            if buffer:
                yield self.speak(buffer)

        async for frame in super().tts_node(spoken(), model_settings):
            yield frame

    def speak(self, text: str) -> str:
        out, pairs = refill.speakable(text)
        if pairs:
            state = self.session.userdata
            state["spoken"] = [
                *state["spoken"],
                *({"written": w, "spoken": s} for w, s in pairs),
            ][-3:]
            publish_refill(self.room, state)
        return out


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="pharmacy-refill")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        userdata=refill.initial_state(),
        stt=make_stt(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
        vad=ctx.proc.userdata["vad"],
    )
    agent = RefillLine(ctx.room)
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()
    agent.watch_transcript()
    publish_refill(ctx.room, session.userdata)
    await session.generate_reply(
        instructions="Greet the caller and ask which medication they want to refill."
    )


if __name__ == "__main__":
    cli.run_app(server)
