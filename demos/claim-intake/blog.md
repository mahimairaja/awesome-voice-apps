---
title: An auto claim intake agent that will not take a bad field
summary: A first-notice-of-loss voice agent that validates every field before it lands, on AssemblyAI, Gemini 3 Flash, and Inworld.
---

## The problem

First notice of loss is the least glamorous, highest-volume call an insurer
takes: someone just had an accident and has to read a policy number and a date
into a phone tree. The job is narrow and rules-heavy: capture eight fields, hold
the line on the two that have to be well-formed, and file.

## Why this stack

Three new-generation providers, one per role. AssemblyAI Universal-3 Pro
transcribes the messy parts (a spelled-out policy number, a plate) accurately.
Gemini 3 Flash drives the turn-by-turn collection and converts spoken dates to
`YYYY-MM-DD`. Inworld speaks the read-back. Turn-taking is LiveKit's inference
VAD and turn detector, so there is no local model to download.

## The interesting part

The whole agent is one claim dict and two tools. `record_field` is generic — one
`Literal` of field names, one `VALIDATORS` map — so adding a field is a line, not
a tool:

```python
ok, result = VALIDATORS[field](value)
if not ok:
    return f"rejected: {result}. Ask the caller again."
claim[field] = result
_publish_claim(self.room, claim)
```

`file_claim` refuses until every field is present, so the model can never file a
half-claim no matter how the conversation wanders.

## The one gotcha

The date validator compares against `date.today()` captured at startup, not a
literal, so "the date of loss cannot be in the future" keeps meaning that a year
from now. The eval feeds a fixed past date and lets the model do the
speech-to-`YYYY-MM-DD` conversion, which is exactly the part worth testing.

## Run it

Talk to it at https://playground.mahimai.ca/demos/claim-intake, or fork the
cookbook and run the worker locally.
