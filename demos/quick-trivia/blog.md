---
title: How to build a voice trivia game with an editable quiz
summary: A three-question voice trivia host whose quiz the caller edits by typing in the playground or by voice, built on a reverse data channel that carries edits back to the agent.
author: Mahimai
github: mahimairaja
---

Every other demo pushes UI one direction: the agent draws, the screen shows.
This one lets the caller edit what is on screen and sends the change back to the
agent. It is a three-question trivia game whose quiz you can retype in the panel
or change by voice, then play.

The voice path is the repo default: Deepgram Nova-3 for STT, `gpt-4o-mini` for
grading and flow, Cartesia Sonic-2 for the host. The model, not code, decides
whether an answer is right and accepts paraphrases.

The new piece is the reverse channel. The agent publishes the quiz as an
`EditableTable`, and edits come back on a `ui_action` topic, the mirror of the
forward `ui` channel the agent publishes on:

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

A `set_question` tool mutates the same `userdata` from voice, so typing and
telling the host hit one source of truth and can never disagree.

The edit application is defensive: a half-finished grid must never wipe a
question, so a blanked cell falls back to the current value and the row count
stays fixed:

```python
q = str(row[0]).strip() if len(row) > 0 else ""
a = str(row[1]).strip() if len(row) > 1 else ""
updated.append({"q": q or item["q"], "a": a or item["a"]})
```

Play runs on two tools: `ask_question` shows a Card and hands the model the
answer to judge, and `score_answer` records the result. `score_answer` keeps a
set of already-scored questions, so the total can never run past three.

> [!TIP]
> The reverse channel is just another data topic. Publish interactive UI on
> `ui`, subscribe to `ui_action` for what comes back, and match on the
> component's `id` and `action` so you only react to the control you meant to.

Build it from an empty folder in the full walkthrough, or talk to the finished
agent at https://playground.mahimai.ca/demos/quick-trivia.
