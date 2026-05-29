"""quick-trivia: voice trivia host.

Quizzes the caller with one short trivia question at a time and keeps score.

Run it:
1. Copy templates/livekit-base/.env.example to .env and fill the six keys.
2. uv sync
3. uv run --no-project python agent.py download-files
4. uv run --no-project python agent.py dev, then open
   https://playground.mahimai.ca/demos/quick-trivia.
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

QUESTIONS = [
    {"q": "What planet is closest to the sun?", "a": "Mercury"},
    {"q": "How many sides does a hexagon have?", "a": "Six"},
    {"q": "What is the chemical symbol for water?", "a": "H2O"},
    {"q": "Who wrote Romeo and Juliet?", "a": "Shakespeare"},
    {"q": "What is the largest ocean on Earth?", "a": "Pacific Ocean"},
    {"q": "How many bones are in the adult human body?", "a": "206"},
    {"q": "What is the capital of Japan?", "a": "Tokyo"},
    {"q": "What year did World War Two end?", "a": "1945"},
    {"q": "What is the square root of 144?", "a": "12"},
    {"q": "What gas do plants absorb from the air?", "a": "Carbon dioxide"},
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

    def log_publish_failure(t: asyncio.Task[None]) -> None:
        try:
            t.result()
        except Exception:
            logger.exception("failed to publish playground ui event")

    task.add_done_callback(log_publish_failure)


def _ui_action(room: rtc.Room, component_id: str) -> Literal["mount", "update"]:
    mounted = getattr(room, "_ui_mounted", None)
    if mounted is None:
        mounted = set()
        setattr(room, "_ui_mounted", mounted)
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
        props={"correct": correct, "total": total, "outOf": len(QUESTIONS)},
    )


def _questions_prompt() -> str:
    return "\n".join(
        f"{i}. Q: {item['q']}  A: {item['a']}"
        for i, item in enumerate(QUESTIONS, 1)
    )


class TriviaHost(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                "You are an upbeat voice trivia host. Ask the caller the ten "
                "questions below, one at a time, in order. After the caller "
                "answers, decide if it is correct (accept reasonable "
                "paraphrases), then call score_answer with was_correct. "
                "Tell the caller whether they got it right and reveal the "
                "correct answer if they missed it. Then ask the next question. "
                "After all ten, announce their final score and wrap up. "
                "Keep replies short, plain text, no markdown or emojis.\n\n"
                f"Questions:\n{_questions_prompt()}"
            ),
        )
        self.room = room

    @function_tool()
    async def score_answer(
        self,
        context: RunContext[dict],
        was_correct: bool,
    ) -> str:
        """Record whether the caller answered correctly and update the score card.

        Call this once per question, after you have judged the caller's answer.
        """
        data = context.userdata
        data["total"] += 1
        if was_correct:
            data["correct"] += 1
        _publish_score(self.room, data["correct"], data["total"])
        return f"Score updated: {data['correct']}/{data['total']}"


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="quick-trivia")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    userdata: dict = {"correct": 0, "total": 0}
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
        instructions="Welcome the caller and ask question one."
    )


if __name__ == "__main__":
    cli.run_app(server)
