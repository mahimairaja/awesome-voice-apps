---
title: How to build a voice trivia game with an editable quiz
summary: Build a three-question voice trivia host whose quiz the caller edits by typing or by voice, on a reverse data channel that carries edits back to the agent, from an empty folder to a running worker.
author: Mahimai
github: mahimairaja
---

## What you will build

A voice trivia host. Three questions and their answers show on screen at the
start. The caller keeps them, types over any in an editable panel, or tells the
host to change one, then plays: one question at a time, spoken answers,
paraphrases counted, a running score. The point is not the game. It is that the
screen can edit data and send it back to the agent. The finished demo is
`quick-trivia`.

You need three provider keys (Deepgram, OpenAI, Cartesia) and three LiveKit
values. And `uv`.

## 1. Scaffold

Pin Python to `3.11`. `pyproject.toml` pulls the three repo-default plugins:

```toml
[project]
name = "quick-trivia"
version = "0.1.0"
description = "Voice trivia host that quizzes callers and keeps score."
requires-python = ">=3.11"
dependencies = [
    "livekit-agents[deepgram,openai,cartesia]>=1.6,<2.0",
    "python-dotenv>=1.0",
]
```

`.env.example` lists the six keys (LIVEKIT trio plus OPENAI, DEEPGRAM,
CARTESIA).

## 2. The quiz state

Three seed questions, copied per session into `userdata` so a caller's edits
never bleed across calls:

```python
DEFAULT_QUESTIONS = [
    {"q": "What planet is closest to the sun?", "a": "Mercury"},
    {"q": "How many sides does a hexagon have?", "a": "Six"},
    {"q": "What is the chemical symbol for water?", "a": "H2O"},
]
```

The session userdata holds the questions plus the score bookkeeping: `correct`,
`total`, a `scored` set, the `mounted` set, and a `started` flag.

## 3. The reverse channel

Every other demo pushes UI one way, agent to screen. Here the agent publishes
the quiz as an `EditableTable`, and edits come back on a `ui_action` topic, the
mirror of the forward `ui` channel. The entrypoint subscribes and applies them:

```python
@ctx.room.on("data_received")
def on_ui_action(packet: rtc.DataPacket) -> None:
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
```

> [!IMPORTANT]
> Match on the component's `id` and `action` before you react, so the agent only
> handles the control it meant to. The handler also ignores edits once the quiz
> has started.

The edit application is defensive: a half-finished grid must never wipe a
question, so a blanked cell falls back to the current value and the row count
stays fixed at three:

```python
def _apply_quiz_edit(data: dict, rows: object) -> bool:
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
```

## 4. Edit by voice, too

A `set_question` tool mutates the same `userdata`, so typing in the grid and
telling the host aloud hit one source of truth and can never disagree. It
refuses once the quiz has started and re-publishes the editor after a change.

## 5. Play, and score honestly

`ask_question` shows the question on a Card and hands the model the answer to
judge (never spoken). The first call starts the quiz and unmounts the editor, so
the answers leave the screen. `score_answer` records the result, and keeps a set
of already-scored questions so a repeat call is a no-op and the total can never
run past three:

```python
if question_number in data["scored"]:
    return (
        f"Question {question_number} is already scored. "
        f"The score stays {data['correct']}/{data['total']}."
    )
data["scored"].add(question_number)
data["total"] += 1
if was_correct:
    data["correct"] += 1
```

The bound `0 <= correct <= total <= 3` holds no matter how the model behaves.

## 6. The eval

The behavioral test runs the agent in text mode against the real `gpt-4o-mini`
and scores the conversation with LiveKit's `evals` judges. It keeps the default
quiz, plays, answers "Mercury" to the first question, and asserts the judges
pass:

```python
await session.run(user_input="Keep these questions and start the quiz.")
await session.run(user_input="Mercury.")

judges = JudgeGroup(
    llm=judge_llm,
    judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
)
evaluation = await judges.evaluate(session.history)
assert evaluation.all_passed
```

## 7. Run it

```sh
cp .env.example .env
```

```sh
uv sync
```

```sh
uv run python agent.py dev
```

Open https://playground.mahimai.ca/demos/quick-trivia, type a new question into
the grid before the game starts, then watch the host quiz you on your version
instead of its own.
