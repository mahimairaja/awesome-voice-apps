---
title: A voice trivia host that mirrors its score into live UI
author: Mahimai
summary: A ten-question voice trivia game that lets the model grade spoken answers and pushes a running score into the playground Score card, plus the state lesson when the LLM owns both grading and flow.
---

## The problem

A trivia host reads one short general-knowledge question at a time, hears
a spoken answer, decides if it is right (a paraphrase still counts),
reveals the answer on a miss, and keeps a running score. Ten questions,
then a final tally. The score is the showpiece: this is the cookbook's
canonical demo of the playground's generative-UI Score component for the
education category. So the real audience is Mahimai AI showing off live
score UI, plus anyone who wants a self-contained voice quiz to run locally
and talk to.

## Why this stack

The voice path is the template default: Deepgram Nova-3 for STT, Cartesia
Sonic-2 voicing the upbeat host, Silero VAD prewarmed once in prewarm and
reused across sessions. The turn detector is LiveKit MultilingualModel,
which matters for a one-question cadence: the model judges only after the
caller has finished answering. The grading itself is OpenAI gpt-4o-mini.
The prompt tells it to decide if the answer is correct, accepting
paraphrases, then call score_answer with was_correct. The model, not code,
is the grader.

## The interesting part

The playground protocol wants exactly one mount per component id, then
update for every change after. There is no separate state object tracking
that, so a mounted set lives in userdata and a helper derives the action:

```python
def _ui_action(
    mounted: set[str], component_id: str
) -> Literal["mount", "update"]:
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
            "outOf": len(QUESTIONS),
        },
    )
```

The first publish mounts the 0/0 card after connect, every later one
updates it. Both the score and the mount bookkeeping live in one userdata
dict, so the UI never disagrees with state.

## What surprised me

When you hand the LLM both grading and flow control, the only thing
keeping the scorecard honest is the tool. score_answer increments total on
every call and the prompt drives the whole quiz, so a single misfire
(calling the tool twice for one answer, or running past question ten)
would publish 11/10 against outOf 10: a progress bar past full, no error.
The fix is an advancing question index in userdata. score_answer refuses
once index reaches len(QUESTIONS) and returns the final tally instead of
counting again, which holds the invariant 0 <= correct <= total <= outOf
no matter how the model behaves.

## Run it

Talk to it at https://playground.mahimai.ca/demos/quick-trivia. Or fork
the cookbook and run the worker locally.
