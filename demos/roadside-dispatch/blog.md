---
title: A roadside dispatcher that scores the audio before STT runs
summary: A breakdown-call voice agent that runs Tyto audio scoring locally and refuses to dispatch on fields it captured while the line was bad. Plus the lesson that packet loss cannot be faked from a laptop, so the gate keys off overall risk instead of any single dimension.
author: Mahimai
---

## The problem

A roadside assistance call often fails before the LLM ever sees it. The caller is on the shoulder of a highway: wind and traffic in the mic, a passenger talking over them, the phone crammed against a car door. STT hears garbled audio and transcribes something plausible but wrong. The agent captures "Route 15" when the caller said "Route 50," and the tow truck goes to the wrong place.

The standard fix is a human who says "could you repeat that?" A voice agent does not know when to ask. It sees only the transcript, and a confident wrong transcript looks exactly like a confident right one. **Tyto** scores the audio itself, before STT runs, so the agent can tell the difference.

![Where Tyto sits in the voice pipeline](https://assets.mahimai.ca/tyto-pipeline.svg)

## Why this stack

The base stack is the cookbook default, and that is the point: Deepgram Nova-3 for STT, OpenAI gpt-4o-mini for the dispatch logic, Cartesia Sonic-2 for TTS. Any of the three swaps out with a one-line change. None of them is what makes this demo interesting, so I picked the baseline I trust and moved on.

The one addition is ai-coustics Tyto (`tyto-l-16khz`). Tyto scores a rolling five-second window of the caller's audio across six dimensions and emits a single risk score from 0 to 1. It scores; it does not clean. The audio Tyto reads is the exact audio Deepgram reads — Tyto just gets an opinion about it first.

That opinion is the whole feature: a number the dispatch logic can act on before it trusts a transcript. And it costs almost nothing to get, because Tyto runs locally on CPU inside the agent process. The audio buffer never leaves the machine. [The SDK does phone home to authorize and report usage — but it sends a license check, not your caller's location.] For a roadside call, where someone stranded is reading a mile marker out loud, "the audio never leaves" is not a nice-to-have.

## The interesting part

The scoring loop buffers mono float32 samples from the caller's audio track and runs Tyto once per second over a five-second window. Raw scores are jumpy by design — a single passing truck spikes the noise dimension for one window — so each raw score folds into an exponential moving average before anything acts on it. One loud moment nudges the risk; it does not redline it.

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

The smoothed risk is what the agent acts on. At the moment it captures a dispatch field, it reads the current risk band and tags the field as either `clean` or `needs_confirmation` — so the tag records how good the audio was _when that specific fact was heard_, not how good it is now.

```python
def field_state(self) -> str:
    return "clean" if self.band == "good" else "needs_confirmation"

def _capture(self, name: str, value: str) -> str:
    state = self.health.field_state()
    self.fields[name] = {"value": value, "state": state}
    _publish_details(self.room, self.fields)
    return state
```

The `dispatch` tool then refuses to fire until every critical field — location, vehicle, plate, callback — is marked clean. A field captured during a bad window stays blocked until the agent reads it back and the caller confirms it. The agent does not re-ask everything when the line is poor; it re-asks only the facts it was unsure of, and only the ones that would send a truck to the wrong place.

## What surprised me

I expected to gate dispatch on individual dimensions — block on high `packet_loss`, block on high `noise`, and so on. That fell apart on the first real test, for a dull reason: you cannot fake packet loss from a laptop. Noise, reverb, and background media are easy to trigger on demand (play a video, step back from the mic, turn on a fan). Packet loss is a network transport artifact. Sitting at a desk on stable Wi-Fi, that dimension never moves, so a gate keyed to it would never fire in any demo I could actually perform. The gate had to key off the overall smoothed risk score, which moves for _all_ the triggerable degradations. The single-dimension version was tidier on paper and untestable in the room.

The other surprise was the barge-in toggle. In livekit-agents 1.x it is not `allow_interruptions` on the session — that attribute shows up in early docs and older examples, but in 1.6 it is deprecated and not stored as a settable option. The live path is `session.options.turn_handling["interruption"]["enabled"]`, a nested TypedDict that is mutable at runtime. The helper wraps it defensively because that nested structure is exactly the kind of thing that moves between minor versions:

```python
def _set_barge_in(session: AgentSession, *, enabled: bool) -> None:
    try:
        session.options.turn_handling["interruption"]["enabled"] = enabled
    except (AttributeError, KeyError, TypeError):
        logger.warning("could not toggle barge-in on this livekit-agents version")
```

Barge-in suppression fires when `interfering_speech` crosses its threshold for two consecutive windows and reverts when the smoothed value clears — so a second voice in the car stops the agent from treating background chatter as the caller, then hands control back once that voice is gone. Without the version-safe fallback, a routine agents upgrade would silently stop toggling barge-in, with no error and no log line to tell you it had quietly become a no-op.

## Run it

Clone the cookbook, fill `.env` with the seven keys, and `uv sync`. Download the Tyto model once with

```bash
python agent.py download-files
```

then start the worker with

```bash
python agent.py dev
```

Open [playground.mahimai.ca/demos/roadside-dispatch](https://playground.mahimai.ca/demos/roadside-dispatch), paste your LiveKit keys, and connect. The risk readout and the six dimension bars go live within a few seconds of you speaking.

<br/>

To see the gate work: play with the noise slider in the UI, then watch the agent refuse to dispatch and read the location back to you before it accepts it.
