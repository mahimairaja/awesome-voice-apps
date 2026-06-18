"""quick-trivia: voice trivia host with a quiz the caller can edit.

Three trivia questions show on screen at the start of the call, answers and
all. The caller keeps them, types over any in the editable panel, or tells the
host to change one, then plays the quiz the host now asks from the edited set.

Run it:
1. Copy .env.example to .env and fill the six keys.
2. uv sync
3. uv run --no-project python agent.py dev, then open
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
    inference,
)
from livekit.plugins import cartesia, deepgram, openai

load_dotenv()

logger = logging.getLogger(__name__)

# The reverse data channel: edits the playground sends back to the agent.
# Mirror of the forward "ui" topic publish_ui_event uses.
UI_ACTION_TOPIC = "ui_action"

# Seed questions. Copied per session into userdata so a caller's edits never
# bleed across calls. Three is enough to demonstrate the loop.
DEFAULT_QUESTIONS = [
    {"q": "What planet is closest to the sun?", "a": "Mercury"},
    {"q": "How many sides does a hexagon have?", "a": "Six"},
    {"q": "What is the chemical symbol for water?", "a": "H2O"},
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


def _ui_action(mounted: set[str], component_id: str) -> Literal["mount", "update"]:
    if component_id in mounted:
        return "update"
    mounted.add(component_id)
    return "mount"


def _publish_score(room: rtc.Room, data: dict) -> None:
    publish_ui_event(
        room,
        "Score",
        _ui_action(data["mounted"], "score"),
        component_id="score",
        props={
            "correct": data["correct"],
            "total": data["total"],
            "outOf": len(data["questions"]),
        },
    )


def _publish_quiz_editor(room: rtc.Room, data: dict) -> None:
    """Show the editable quiz (question + answer per row) during setup."""
    publish_ui_event(
        room,
        "EditableTable",
        _ui_action(data["mounted"], "quiz"),
        component_id="quiz",
        props={
            "title": "your quiz",
            "columns": ["Question", "Answer"],
            "rows": [[item["q"], item["a"]] for item in data["questions"]],
            "submitLabel": "Use these",
            "actionId": "quiz",
        },
    )


def _unmount_quiz_editor(room: rtc.Room, data: dict) -> None:
    """Clear the editor once play starts so the answers leave the screen."""
    publish_ui_event(room, "EditableTable", "unmount", component_id="quiz")
    data["mounted"].discard("quiz")


def _publish_question(room: rtc.Room, data: dict, n: int, *, result: bool | None = None) -> None:
    """Show question n on a Card. result None while asking; True/False after scoring.

    All props are always sent because the playground merges card updates: when a
    new question replaces a scored one, an empty subtitle/footer clears the old
    correct/missed marker instead of leaving it behind.
    """
    item = data["questions"][n - 1]
    if result is None:
        subtitle, footer = "", ""
    elif result:
        subtitle, footer = "correct", f"answer: {item['a']}"
    else:
        subtitle, footer = "missed", f"answer: {item['a']}"
    publish_ui_event(
        room,
        "Card",
        _ui_action(data["mounted"], "question"),
        component_id="question",
        props={
            "subtitle": subtitle,
            "title": f"Question {n} of {len(data['questions'])}",
            "body": item["q"],
            "footer": footer,
            "accent": True,
        },
    )


def _apply_quiz_edit(data: dict, rows: object) -> bool:
    """Replace the quiz from edited rows. Keeps the row count fixed and fills a
    blanked cell from the current value, so a half-finished edit never wipes a
    question. Returns False (no re-publish) when rows are not a usable grid.
    """
    if not isinstance(rows, list):
        return False
    current = data["questions"]
    updated = []
    for i, item in enumerate(current):
        row = rows[i] if i < len(rows) and isinstance(rows[i], list) else []
        q = str(row[0]).strip() if len(row) > 0 else ""
        a = str(row[1]).strip() if len(row) > 1 else ""
        updated.append({"q": q or item["q"], "a": a or item["a"]})
    data["questions"] = updated
    return True


class TriviaHost(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                "You are an upbeat voice trivia host. The caller sees three "
                "trivia questions and their answers on screen at the start. "
                "First invite them to keep the questions or change any: they can "
                "type over a question or answer in the panel and press save, or "
                "tell you the change aloud. When they say a change aloud, call "
                "set_question with the question_number, the new question, and the "
                "new answer. "
                "When the caller is ready, play the quiz. For each question in "
                "order, call ask_question with its number first; that shows it on "
                "screen and gives you the answer to judge. Read only the question "
                "aloud, never the answer. After the caller answers, decide if "
                "they are right (accept reasonable paraphrases) and call "
                "score_answer with the question_number and was_correct. Tell them "
                "if they got it, and reveal the answer only when they missed it. "
                "After the last question, announce the final score and wrap up. "
                "Keep replies short, plain text, no markdown or emojis."
            ),
        )
        self.room = room

    @function_tool()
    async def set_question(
        self,
        context: RunContext[dict],
        question_number: int,
        question: str,
        answer: str,
    ) -> str:
        """Replace a question and its answer during setup, before the quiz starts.

        Call this when the caller tells you a change aloud. Pass the
        question_number (1 to 3), the new question, and its answer.
        """
        data = context.userdata
        if data["started"]:
            return "The quiz already started, so the questions are locked now."
        n = len(data["questions"])
        if not 1 <= question_number <= n:
            return f"There are {n} questions; pick 1 to {n}."
        q = question.strip()
        a = answer.strip()
        if not q or not a:
            return "I need both a question and its answer to set it."
        data["questions"][question_number - 1] = {"q": q, "a": a}
        _publish_quiz_editor(self.room, data)
        return f"Question {question_number} is now: {q} (answer {a})."

    @function_tool()
    async def ask_question(
        self,
        context: RunContext[dict],
        question_number: int,
    ) -> str:
        """Show a question on screen, then read it to the caller.

        Call this with the question_number (1 to 3) right before you ask the
        question aloud. The first call starts the quiz and clears the editor.
        Returns the question to read and, for your judging only, its answer.
        """
        data = context.userdata
        n = len(data["questions"])
        if not 1 <= question_number <= n:
            return f"There is no question {question_number}; this quiz has {n}."
        if not data["started"]:
            data["started"] = True
            _unmount_quiz_editor(self.room, data)
        _publish_question(self.room, data, question_number)
        item = data["questions"][question_number - 1]
        return (
            f"Showing question {question_number}. Read this aloud: {item['q']} "
            f"For your judging only, do not say it: the answer is {item['a']}."
        )

    @function_tool()
    async def score_answer(
        self,
        context: RunContext[dict],
        question_number: int,
        was_correct: bool,
    ) -> str:
        """Record whether the caller answered a question correctly and update the card.

        Call this once per question, after you have judged the answer. Pass
        question_number (1 to 3) for the question you just asked.
        """
        data = context.userdata
        n = len(data["questions"])
        if not 1 <= question_number <= n:
            return f"There is no question {question_number}; this quiz has {n}."
        if question_number in data["scored"]:
            return (
                f"Question {question_number} is already scored. "
                f"The score stays {data['correct']}/{data['total']}."
            )
        data["scored"].add(question_number)
        data["total"] += 1
        if was_correct:
            data["correct"] += 1
        _publish_question(self.room, data, question_number, result=was_correct)
        _publish_score(self.room, data)
        answer = data["questions"][question_number - 1]["a"]
        tail = "correct" if was_correct else f"answer: {answer}"
        if data["total"] == n:
            return (
                f"Question {question_number} scored ({tail}). Final score: {data['correct']}/{n}."
            )
        return f"Question {question_number} scored ({tail}): {data['correct']}/{data['total']}."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = inference.VAD()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="quick-trivia")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    userdata: dict = {
        "questions": [dict(item) for item in DEFAULT_QUESTIONS],
        "correct": 0,
        "total": 0,
        "scored": set(),
        "mounted": set(),
        "started": False,
    }

    @ctx.room.on("data_received")
    def on_ui_action(packet: rtc.DataPacket) -> None:
        # Clickable edits from the EditableTable panel. Ignored once the quiz
        # has started (the editor is unmounted by then anyway).
        if packet.topic != UI_ACTION_TOPIC or userdata["started"]:
            return
        try:
            envelope = json.loads(packet.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            logger.exception("failed to decode ui_action payload")
            return
        if envelope.get("id") != "quiz" or envelope.get("action") != "submit":
            return
        rows = (envelope.get("payload") or {}).get("rows")
        if _apply_quiz_edit(userdata, rows):
            _publish_quiz_editor(ctx.room, userdata)

    session = AgentSession(
        userdata=userdata,
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-2"),
        vad=ctx.proc.userdata["vad"],
        turn_detection=inference.TurnDetector(),
    )

    await session.start(agent=TriviaHost(ctx.room), room=ctx.room)
    await ctx.connect()
    _publish_score(ctx.room, userdata)
    _publish_quiz_editor(ctx.room, userdata)
    await session.generate_reply(
        instructions=(
            "Welcome the caller, tell them the three questions are on screen, "
            "and invite them to keep them or change any before you start."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
