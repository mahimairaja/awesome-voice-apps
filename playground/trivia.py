"""Three-question voice trivia, with deterministic scoring and per-call state."""

import re

from hosted_coffee import publish_ui_event
from livekit.agents import Agent, RunContext, function_tool

QUESTIONS = (
    ("Which planet is closest to the Sun?", "Mercury", {"mercury"}),
    ("How many sides does a hexagon have?", "Six", {"six", "6"}),
    ("What is the chemical symbol for water?", "H2O", {"h2o", "h two o", "h two oh", "h 2 o"}),
)


def initial_state():
    return {"index": 0, "correct": 0, "answers": {}}


def publish_trivia(room, data):
    index = data["index"]
    publish_ui_event(
        room,
        "Trivia",
        "update",
        {
            "question": QUESTIONS[index][0] if index < len(QUESTIONS) else "Round complete.",
            "number": min(index + 1, len(QUESTIONS)),
            "correct": data["correct"],
            "answered": index,
            "outOf": len(QUESTIONS),
            "complete": index == len(QUESTIONS),
        },
    )


class HostedTriviaHost(Agent):
    def __init__(self, room):
        super().__init__(
            instructions=(
                "You host a three-question voice trivia game. Ask only the question shown "
                "by the game, one at a time. Never reveal an answer before the caller tries. "
                "When they answer, call answer_question with the current question number "
                "and their spoken answer. Scoring is decided by the tool, not by you. "
                "If the caller asks to skip, submit the word skip. Read the returned next "
                "question and wait. Repeat the current question on request. Keep replies "
                "short. After the third question, announce the score and thank them."
            )
        )
        self.room = room

    @function_tool()
    async def answer_question(
        self, context: RunContext[dict], question_number: int, answer: str
    ) -> str:
        """Score the caller's answer once, then return the next question."""
        data = context.userdata
        if question_number in data["answers"]:
            return data["answers"][question_number]
        if question_number != data["index"] + 1 or data["index"] >= len(QUESTIONS):
            return "Answer the current question first."
        _question, expected, accepted = QUESTIONS[data["index"]]
        normalized = re.sub(r"[^a-z0-9 ]", "", answer.lower()).strip()
        correct = normalized in accepted
        data["correct"] += int(correct)
        data["index"] += 1
        result = "Correct." if correct else f"The answer was {expected}."
        if data["index"] < len(QUESTIONS):
            result += f" Next question: {QUESTIONS[data['index']][0]}"
        else:
            result += f" Final score: {data['correct']} of {len(QUESTIONS)}."
        data["answers"][question_number] = result
        publish_trivia(self.room, data)
        return result
