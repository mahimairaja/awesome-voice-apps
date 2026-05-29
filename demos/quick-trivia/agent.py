"""quick-trivia: voice trivia quiz agent.

Asks one short trivia question at a time, accepts the caller's answer,
tracks correct vs. total, and keeps a live score card on the playground.

Run it:
1. Copy templates/livekit-base/.env.example to .env and fill the six keys.
2. Run uv sync.
3. Run uv run --no-project python agent.py download-files.
4. Run uv run --no-project python agent.py dev, then open the LiveKit Agents Playground.
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
from livekit.plugins import cartesia, deepgram, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()

logger = logging.getLogger(__name__)


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


def _publish_score(room: rtc.Room, correct: int, total: int) -> None:
    publish_ui_event(
        room,
        "Score",
        _ui_action(room, "score"),
        component_id="score",
        props={
            "correct": correct,
            "total": total,
            "label": f"{correct} / {total}",
        },
    )


class TriviaHost(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                "You are a friendly trivia host. Ask one short trivia question at a time. "
                "Wait for the caller to answer before revealing if they are right or wrong. "
                "When they answer, tell them correct or incorrect and give the right answer "
                "when they are wrong. Call score_answer with was_correct set to true or false "
                "before moving to the next question. Keep replies short, plain text, no "
                "markdown or emojis. Draw questions from science, geography, history, pop "
                "culture, and sports. Stop after 10 questions and announce the final score."
            ),
        )
        self.room = room

    @function_tool()
    async def score_answer(
        self,
        context: RunContext[dict],
        was_correct: bool,
    ) -> str:
        """Record whether the caller's last answer was correct and update the score card.

        Call this once after evaluating every answer, before asking the next question.
        """
        score = context.userdata["score"]
        score["total"] += 1
        if was_correct:
            score["correct"] += 1
        _publish_score(self.room, score["correct"], score["total"])
        return f"Score updated: {score['correct']} correct out of {score['total']}."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="quick-trivia")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    userdata = {"score": {"correct": 0, "total": 0}}
    session = AgentSession(
        userdata=userdata,
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-2"),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )

    await session.start(agent=TriviaHost(ctx.room), room=ctx.room)
    await ctx.connect()
    _publish_score(ctx.room, 0, 0)
    await session.generate_reply(
        instructions="Greet the caller, tell them you will ask 10 trivia questions, and ask the first one."
    )


if __name__ == "__main__":
    cli.run_app(server)
