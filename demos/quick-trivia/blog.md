---
title: A voice trivia host with a quiz the caller can edit
author: Mahimai
summary: A three-question voice trivia game whose quiz the visitor edits by typing in the playground or by voice, built on a new reverse data channel that carries edits back to the agent.
---

## The problem

The host shows three trivia questions and their answers on screen at the
start of the call. The caller keeps them, types over any in the panel, or
tells the host to change one, then plays. One question at a time, a spoken
answer, paraphrases count, the answer revealed on a miss, a running score,
a final tally. The showpiece is no longer just the score card: it is the
playground editing data and sending it back to the agent.

## Why this stack

The voice path is the template default: Deepgram Nova-3 for STT, Cartesia
Sonic-2 voicing the upbeat host, and LiveKit inference for the rest:
`inference.VAD` prewarmed once and reused across sessions, and
`inference.TurnDetector` so the host judges only after the caller has
finished answering. The grading is OpenAI gpt-4o-mini. The prompt tells it
to decide if an answer is correct, accepting paraphrases, then call
score_answer. The model, not code, is the grader.

## The interesting part

The playground has only ever pushed UI one way, agent to screen. Letting
the visitor edit the quiz means a return path. A new `ui_action` topic, the
mirror of the forward `ui` channel, carries the edited grid back. The agent
subscribes and updates its quiz:

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

The same questions are editable by voice (a set_question tool) mutating the
same userdata. One source of truth, two ways in. When the quiz starts, the
editor unmounts so the answers leave the screen.

## What surprised me

Two things. First, the edit has to be defensive. A half-finished grid (a
blanked answer cell) must not wipe a question, so `_apply_quiz_edit` fills a
blank cell from the current value and keeps the row count fixed at three, so
the score math stays clean.

Second, the old invariant still holds. Hand the LLM both grading and flow
and the only honest scorekeeper is the tool. score_answer keeps a set of
already-scored question numbers, so a repeat call is a no-op and total can
never run past the three questions. The bound 0 <= correct <= total <= 3
holds no matter how the model behaves.

## Run it

Talk to it at https://playground.mahimai.ca/demos/quick-trivia. Or fork the
cookbook and run the worker locally.
