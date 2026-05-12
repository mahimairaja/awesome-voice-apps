"""livekit-base: starter scaffold for awesome-voice-apps demos.

Copy this folder into demos/<slug>/ to start a new demo.
Customize the Assistant instructions, add @function_tool methods, or
swap any of the providers below. Keep net new code under ~300 lines.
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
    cli,
)
from livekit.plugins import cartesia, deepgram, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()

logger = logging.getLogger(__name__)


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
    id: str | None = None,
) -> None:
    """Publish a playground UI event; see protocol.ts and demo component registries."""
    envelope = {
        "type": "ui_event",
        "component": component,
        "action": action,
        "props": props or {},
    }
    if id is not None:
        envelope["id"] = id

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


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a friendly voice assistant for the awesome-voice-apps "
                "cookbook. Keep replies short, plain text, no markdown or emojis. "
                "If a user asks what you are, say you are a starter scaffold "
                "meant to be customized."
            ),
        )


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="livekit-base")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-2"),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )

    await session.start(agent=Assistant(), room=ctx.room)
    await ctx.connect()
    await session.generate_reply(
        instructions="Greet the user and offer your help."
    )


if __name__ == "__main__":
    cli.run_app(server)
