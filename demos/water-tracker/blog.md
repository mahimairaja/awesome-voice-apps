---
title: How to build a hydration coach voice agent
summary: A voice hydration coach that logs glasses of water, tracks progress toward a daily goal, and undoes a miscount, on Deepgram, OpenAI, and Cartesia.
author: Mahimai
github: mahimairaja
---

This is the smallest complete tool-using voice agent in the cookbook: a
hydration coach that logs glasses of water by voice, counts toward a daily goal,
and undoes a miscount. If you want the shape of a LiveKit agent with nothing
extra, start here: it is three function tools over one in-memory dict, the same
scaffold every other demo builds on.

The stack is the repo default: Deepgram Nova-3 for STT, `gpt-4o-mini` for the
tools, Cartesia Sonic-2 for the coach. Turn-taking is LiveKit's inference VAD and
turn detector.

The whole state is two integers in `userdata`: glasses and goal. `log_water`
adds to the count, guards against a nonsense value, and re-publishes the Stat:

```python
if glasses < 1:
    return "I can only log one or more glasses; say 'undo' if you miscounted."
data = context.userdata
data["glasses"] += glasses
_publish_stat(self.room, data, data["glasses"], data["goal"])
```

The interesting problem in a counter is not adding, it is undoing, because voice
logging is exactly where miscounts happen. `remove_water` is a first-class tool,
the exact inverse of log, and it floors the total at zero so the count can never
go negative:

```python
removed = min(glasses, data["glasses"])
data["glasses"] = max(0, data["glasses"] - glasses)
_publish_stat(self.room, data, data["glasses"], data["goal"])
```

A third tool, `set_goal`, moves the daily target and refuses a goal under one,
so the bar can never claim success at zero intake. Every tool re-publishes the
same Stat, which draws a progress bar for free from its `of` denominator:

```python
props={
    "label": "glasses today",
    "value": glasses,
    "of": goal,
    "caption": caption,
}
```

> [!TIP]
> Give every mutation an inverse and a guard: log rejects under one, remove
> clamps at zero, set_goal refuses under one. The count stays valid (glasses at
> zero or more, goal at one or more) whatever the caller says.

Build it from an empty folder in the full walkthrough, or talk to the finished
agent at https://playground.mahimai.ca/demos/water-tracker.
