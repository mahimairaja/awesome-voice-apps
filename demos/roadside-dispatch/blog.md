---
title: How to build a roadside dispatch voice agent that scores the line
summary: A roadside dispatcher that scores the caller's audio with Tyto on a rolling window, adapts when the line degrades, and re-confirms any detail captured over a bad connection, on Deepgram, OpenAI, and Cartesia.
author: Mahimai
github: mahimairaja
---

Roadside calls come from the worst audio in the world: a shoulder next to
traffic, wind, a bad cell connection. STT hears garbled audio and transcribes
something plausible but wrong, and a confident wrong transcript looks exactly
like a right one. This dispatcher scores the audio itself, before STT, so it
knows when to re-confirm.

The voice path is the repo default (Deepgram Nova-3, `gpt-4o-mini`, Cartesia
Sonic-2), plus the ai-coustics Tyto SDK scoring the caller's audio in parallel.
Tyto runs locally on CPU, so the audio never leaves the machine.

A score loop taps the caller's track, keeps a rolling five-second window, and
scores it once a second, folding each raw score into a moving average so one
passing truck does not redline the risk:

```python
chunk = np.asarray(buffer, dtype=np.float32)
results = await asyncio.to_thread(analyzer.analyze, chunk, sample_rate, len(chunk))
if not results:
    continue
result = results[-1]
raw = {name: float(getattr(result, name)) for name in ("risk_score", *DIMENSIONS)}
health.update(raw)
```

Every captured field is stamped with the line quality at the moment it landed. A
field taken over a rough line comes back `needs_confirmation`, so the agent reads
it back before moving on:

```python
def _capture(self, name: str, value: str) -> str:
    state = self.health.field_state()  # clean | needs_confirmation
    self.fields[name] = {"value": value, "state": state}
    _publish_details(self.room, self.fields)
    return state
```

`dispatch` is the invariant: it refuses while any critical field is missing or
still unconfirmed, so help never goes out on a plate heard through static:

```python
unconfirmed = [
    n for n in CRITICAL_FIELDS if self.fields[n]["state"] == "needs_confirmation"
]
if unconfirmed:
    return f"The line was rough. Let me confirm your {', '.join(unconfirmed)} first."
```

> [!IMPORTANT]
> The Tyto model is a local download. Run `uv run python agent.py download-files`
> once before `dev`, and set `AIC_SDK_LICENSE` alongside the usual six keys.

Build it from an empty folder in the full walkthrough, or talk to the finished
agent at https://playground.mahimai.ca/demos/roadside-dispatch.
