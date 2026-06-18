---
title: A voice trivia host with a quiz the caller can edit
author: Mahimai
github: mahimairaja
summary: A three-question voice trivia game whose quiz the visitor edits by typing in the playground or by voice, built on a new reverse data channel that carries edits back to the agent.
---

## The problem

Every demo so far pushes UI one direction: the agent draws, the screen shows. The visitor can talk, but they cannot touch what is on the screen. This one closes that gap.

A trivia host puts three questions and their answers on screen at the start of the call. The caller keeps them, types over any of them in the panel, or tells the host to swap one out, and then plays. One question at a time, spoken answers, paraphrases count, the answer revealed on a miss, a running score, a final tally. The point of the demo is not the game. It is that the screen can now edit data and send it back to the agent.

## Why this stack

The voice path is the template default: Deepgram Nova-3 for STT and Cartesia Sonic-2 voicing the upbeat host. Turn-taking runs on LiveKit inference: `inference.VAD` prewarmed once and reused across sessions, and `inference.TurnDetector` so the host judges only after the caller has actually finished answering.

Grading is OpenAI gpt-4o-mini. The prompt tells it to decide whether an answer is correct, accept paraphrases, and then call `score_answer`. The model, not code, is the grader.

## The interesting part

The playground has only ever pushed UI one way, agent to screen. Letting the visitor edit the quiz means a path back. The new `ui_action` topic is the mirror of the forward `ui` channel: it carries the edited grid from the screen to the agent, which subscribes and updates its own copy of the quiz.

![One source of truth, two ways in](https://assets.mahimai.ca/quick-trivia-reverse-channel-blog.svg)

```python
@ctx.room.on("data_received")
def on_ui_action(packet: rtc.DataPacket) -> None:
    if packet.topic != UI_ACTION_TOPIC or userdata["started"]:
        return
    envelope = json.loads(packet.data.decode("utf-8"))
    if envelope.get("id") != "quiz" or envelope.get("action") != "submit":
        return
    rows = (envelope.get("payload") or {}).get("rows")
    if _apply_quiz_edit(userdata, rows):
        _publish_quiz_editor(ctx.room, userdata)
```

The same questions are editable by voice through a `set_question` tool that mutates the same `userdata`. That is the part worth stealing: one source of truth, two ways in. Typing in the grid and telling the host hit the same state, so they can never disagree. When the quiz starts, the editor unmounts and the answers leave the screen.

## What surprised me

Two things, and both come down to a tool keeping the system honest.

First, the edit has to be defensive. A half-finished grid (say the caller blanks an answer cell mid-edit) must not wipe a question. So `_apply_quiz_edit` fills a blank cell from the current value and pins the row count at three, which keeps the score math clean no matter what the screen sends.

Second, the old invariant from the other demos still holds. Hand the LLM both grading and flow control and the only honest scorekeeper is the tool. `score_answer` keeps a set of the question numbers it has already scored, so a repeat call is a no-op and the total can never run past three. The bound `0 <= correct <= total <= 3` holds no matter how the model behaves.

## Run it

Talk to it at [playground.mahimai.ca/demos/quick-trivia](https://playground.mahimai.ca/demos/quick-trivia): edit a question by typing over a cell in the panel, or tell the host to change one, then play through all three. Or fork the cookbook and run the worker locally.

To see the reverse channel work, type a new question into the grid before the game starts, then watch the host quiz you on your version instead of its own.
