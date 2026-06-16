---
title: A roadside dispatcher that scores the call before STT runs
summary: A breakdown-call voice agent that runs Tyto audio scoring locally, gates dispatch fields on audio quality, and the lesson that packet loss cannot be faked acoustically so the gate had to key off overall risk instead.
author: Mahimai
---

## The problem

A roadside assistance call often fails before the LLM touches it. The
caller is on the shoulder of a highway, wind and traffic in the mic, a
passenger talking over them, the phone crammed against a car door. STT
hears garbled audio and transcribes something plausible but wrong. The
agent captures "Route 15" when the caller said "Route 50". The tow truck
goes to the wrong place.

The standard fix is a human who says "could you repeat that?" But a
voice agent does not know when to ask. It only sees the transcript, and
a confident wrong transcript looks the same as a confident right one.
Tyto gives the agent a view of the audio before STT runs.

## Why this stack

The base stack is the cookbook default: Deepgram Nova-3 for STT, OpenAI
gpt-4o-mini for tool calls and reasoning, Cartesia Sonic-2 for TTS.
On top of that sits one addition: ai-coustics Tyto (`tyto-l-16khz`),
which scores a rolling five-second window of audio across six dimensions
and emits a risk score.

Tyto scores, not cleans. The audio it analyzes is the same audio
Deepgram receives. The point is the score: a number the dispatch logic
can act on. Crucially, the model runs locally on CPU inside the agent
process. No audio leaves the machine to the scoring service (the SDK
license does authorize and telemeter, but the audio buffer does not).
That matters for a roadside call: the caller may be describing their
precise location.

## The interesting part

The scoring loop buffers mono float32 samples from the caller's audio
track and runs Tyto once per second over a five-second window. Each raw
window folds into an exponential moving average so a single loud truck
does not instantly redline the risk bar:

```python
EMA_ALPHA = 0.3

def ema(prev: float | None, value: float, alpha: float = EMA_ALPHA) -> float:
    if prev is None:
        return value
    return alpha * value + (1 - alpha) * prev

def update(self, raw: dict[str, float]) -> None:
    self.risk = ema(self.risk, raw["risk_score"])
    for d in DIMENSIONS:
        self.dims[d] = ema(self.dims.get(d), raw.get(d, 0.0))
```

The field-accuracy gate reads the smoothed risk at capture time and marks
each captured field as either `clean` or `needs_confirmation`:

```python
def field_state(self) -> str:
    return "clean" if self.band == "good" else "needs_confirmation"

def _capture(self, name: str, value: str) -> str:
    state = self.health.field_state()
    self.fields[name] = {"value": value, "state": state}
    _publish_details(self.room, self.fields)
    return state
```

The `dispatch` tool refuses until every critical field (location, vehicle,
plate, callback) is confirmed. An unconfirmed field blocks the call until
the agent reads it back and the caller says yes.

## What surprised me

The barge-in API in livekit-agents 1.x is not `allow_interruptions` on
the session. That attribute appeared in early docs and some older examples,
but it is deprecated and is not stored as a settable option in 1.6. The
live path is `session.options.turn_handling["interruption"]["enabled"]`,
a nested TypedDict that is mutable at runtime. The helper in the agent
is defensive because the structure could change across minor versions:

```python
def _set_barge_in(session: AgentSession, *, enabled: bool) -> None:
    try:
        session.options.turn_handling["interruption"]["enabled"] = enabled
    except (AttributeError, KeyError, TypeError):
        logger.warning("could not toggle barge-in on this livekit-agents version")
```

The suppression fires when `interfering_speech` crosses its threshold for
two consecutive windows, and it reverts when the smoothed value clears.
Without the version-safe fallback, a minor agents upgrade would silently
stop toggling barge-in with no log output.

## Run it

Clone the cookbook, fill `.env` with the seven keys, sync, download the
Tyto model with `uv run --no-project python agent.py download-files`, and
start with `uv run --no-project python agent.py dev`. Then open
[playground.mahimai.ca/demos/roadside-dispatch](https://playground.mahimai.ca/demos/roadside-dispatch).
The risk Stat and the six Meters bars go live within a few seconds of
speaking.
