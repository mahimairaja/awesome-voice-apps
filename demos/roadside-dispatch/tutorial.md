---
title: How to build a roadside dispatch voice agent that scores the line
summary: Build a roadside dispatcher that scores caller audio with Tyto, gates every captured field on the line quality, and refuses to dispatch on a detail heard through static, from an empty folder to a running worker.
author: Mahimai
github: mahimairaja
---

## What you will build

A roadside assistance dispatcher. It takes a breakdown call, captures the
dispatch details (location, vehicle, plate, callback), and sends help. What
makes it different: the ai-coustics Tyto SDK scores the caller's audio in
parallel with STT, so the agent knows when the line is rough and re-confirms any
field it captured over a bad connection before dispatching. The finished demo is
`roadside-dispatch`.

You need the three voice keys (Deepgram, OpenAI, Cartesia), three LiveKit
values, and an `AIC_SDK_LICENSE` from ai-coustics. And `uv`.

## 1. Scaffold

Pin Python to `3.11`. `pyproject.toml` adds `aic-sdk` and `numpy` to the voice
plugins:

```toml
[project]
name = "roadside-dispatch"
version = "0.1.0"
description = "Roadside assistance dispatcher that scores caller audio with Tyto and gates field accuracy."
requires-python = ">=3.11"
dependencies = [
    "livekit-agents[deepgram,openai,cartesia,silero,turn-detector]>=1.5,<2.0",
    "python-dotenv>=1.0",
    "aic-sdk>=2.4,<3.0",
    "numpy>=2.0",
]
```

`.env.example` lists seven keys (the usual six plus `AIC_SDK_LICENSE`).

> [!IMPORTANT]
> The Tyto model is a local download. `prewarm` fetches it, so run
> `uv run python agent.py download-files` once before `dev`.

## 2. Pure audio-health logic

Keep the scoring math in its own module (`health.py`) with no LiveKit, Tyto, or
asyncio, so it is unit-testable in isolation. Raw Tyto scores are jumpy, so each
folds into an exponential moving average before anything acts on it:

```python
def ema(prev: float | None, value: float, alpha: float = EMA_ALPHA) -> float:
    if prev is None:
        return value
    return alpha * value + (1 - alpha) * prev
```

The field gate keys off the smoothed overall risk band, not any single
dimension:

```python
def field_state(self) -> str:
    return "clean" if self.band == "good" else "needs_confirmation"
```

> [!NOTE]
> Gate on overall risk, not one dimension. Packet loss cannot be faked from a
> laptop on stable Wi-Fi, so a gate keyed to it would never fire in a demo. The
> smoothed risk moves for every degradation you can actually trigger.

## 3. The score loop

Tap the caller's track, buffer mono float32 at the native rate, keep a
five-second window, and score once a second on a worker thread so the event loop
never blocks:

```python
if len(buffer) >= window and since_hop >= hop:
    since_hop = 0
    chunk = np.asarray(buffer, dtype=np.float32)
    results = await asyncio.to_thread(analyzer.analyze, chunk, sample_rate, len(chunk))
    if not results:
        continue
    result = results[-1]
    raw = {name: float(getattr(result, name)) for name in ("risk_score", *DIMENSIONS)}
    health.update(raw)
    _publish_health(room, health)
    _publish_risk(room, health)
    _publish_meters(room, health)
    _publish_verdict(room, health)
    await on_window()
```

The UI mirrors the score: a Stat for the risk number, a Meters panel for the six
dimensions, and a status Card.

## 4. Gate every field on the line

Each capture stamps the field with the line quality at the moment it landed, so
the tag records how good the audio was when that fact was heard:

```python
def _capture(self, name: str, value: str) -> str:
    state = self.health.field_state()  # clean | needs_confirmation
    self.fields[name] = {"value": value, "state": state}
    _publish_details(self.room, self.fields)
    return state
```

Each `set_*` tool returns a confirm prompt when the line is rough. `dispatch` is
the invariant: it refuses while any critical field is missing or unconfirmed, so
a truck never goes to a plate heard through static:

```python
unconfirmed = [
    n for n in CRITICAL_FIELDS if self.fields[n]["state"] == "needs_confirmation"
]
if unconfirmed:
    return f"The line was rough. Let me confirm your {', '.join(unconfirmed)} first."
```

## 5. Interventions and barge-in

When a degradation crosses its threshold for two windows, the agent speaks an
intervention line. For a second voice in the car (`interfering_speech`) it also
suppresses barge-in so it stops treating background chatter as the caller. In
livekit-agents 1.x that toggle is a nested option, wrapped defensively so a
version bump cannot silently turn it into a no-op:

```python
def _set_barge_in(session: AgentSession, *, enabled: bool) -> None:
    try:
        session.options.turn_handling["interruption"]["enabled"] = enabled
    except (AttributeError, KeyError, TypeError):
        logger.warning("could not toggle barge-in on this livekit-agents version")
```

The entrypoint also runs idle and max-call watchdogs and ends the call
gracefully after dispatch.

## 6. The eval

`AudioHealth()` with no scores reports a clean line, so a text eval captures
straight through (the bad-line gating needs real audio). The test runs the
capture to dispatch flow and asserts the judges pass:

```python
await session.run(user_input="I'm on Highway 401 near exit 25 and my car won't start.")
await session.run(user_input="It's a blue Honda Civic.")
await session.run(user_input="The plate is A B C 1 2 3.")
await session.run(user_input="You can reach me at 555 010 2020.")
await session.run(user_input="Yes, that's all correct. Please send help.")
```

## 7. Run it

```sh
cp .env.example .env
```

```sh
uv sync
```

```sh
uv run python agent.py download-files
```

```sh
uv run python agent.py dev
```

Open https://playground.mahimai.ca/demos/roadside-dispatch, play with the noise
slider, and watch the agent refuse to dispatch until it reads a rough-line field
back to you.
