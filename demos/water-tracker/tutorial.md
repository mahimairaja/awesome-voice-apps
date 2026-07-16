---
title: How to build a hydration coach voice agent
summary: Build the smallest complete tool-using voice agent in the cookbook, a hydration coach that logs water, tracks a goal, and undoes a miscount, from an empty folder to a running worker, on Deepgram, OpenAI, and Cartesia.
author: Mahimai
github: mahimairaja
---

## What you will build

A voice hydration coach. Say how many glasses you drank and the count moves
toward a daily goal; undo a miscount and it moves back. It is the smallest
complete demo in the cookbook: two values of state, three tools, one Stat card
on screen. This is the place to learn the shape of a LiveKit agent with nothing
extra. The finished demo is `water-tracker`.

You need three provider keys (Deepgram, OpenAI, Cartesia) and three LiveKit
values. And `uv`.

## 1. Scaffold

Pin Python to `3.11`. `pyproject.toml` pulls the three repo-default plugins:

```toml
[project]
name = "water-tracker"
version = "0.1.0"
description = "Logs glasses of water by voice and tracks progress toward a daily goal."
requires-python = ">=3.11"
dependencies = [
    "livekit-agents[deepgram,openai,cartesia]>=1.6,<2.0",
    "python-dotenv>=1.0",
]
```

`.env.example` lists the six keys (the LIVEKIT trio plus OPENAI, DEEPGRAM,
CARTESIA).

## 2. The state and the Stat

The whole state is two integers in `userdata`: `glasses` and `goal`. The screen
is one Stat card that mirrors them. A `_ui_action` helper keeps its mounted-id
set in that same dict, so all of the demo's state sits in one place:

```python
def _ui_action(data: dict, component_id: str) -> Literal["mount", "update"]:
    mounted = data.setdefault("_ui_mounted", set())
    if component_id in mounted:
        return "update"
    mounted.add(component_id)
    return "mount"


def _publish_stat(room: rtc.Room, data: dict, glasses: int, goal: int) -> None:
    remaining = max(0, goal - glasses)
    caption = (
        "Goal reached! Great job." if glasses >= goal else f"{remaining} more to reach your goal."
    )
    publish_ui_event(
        room,
        "Stat",
        _ui_action(data, "water"),
        component_id="water",
        props={"label": "glasses today", "value": glasses, "of": goal, "caption": caption},
    )
```

> [!TIP]
> The Stat `of` value gives a progress bar toward the goal for free, and the
> caption flips to "Goal reached!" the moment the count hits the target.

## 3. Three guarded, reversible tools

`log_water` adds to the count, but rejects anything below one glass rather than
quietly subtracting:

```python
@function_tool()
async def log_water(self, context: RunContext[dict], glasses: int = 1) -> str:
    if glasses < 1:
        return "I can only log one or more glasses; say 'undo' if you miscounted."
    data = context.userdata
    data["glasses"] += glasses
    _publish_stat(self.room, data, data["glasses"], data["goal"])
    ...
```

`remove_water` is its exact inverse. It clamps a removal to what is actually
logged, floors the total at zero, and refuses an empty remove honestly:

```python
@function_tool()
async def remove_water(self, context: RunContext[dict], glasses: int = 1) -> str:
    if glasses < 1:
        return "I can only remove one or more glasses; tell me how many to undo."
    data = context.userdata
    if data["glasses"] == 0:
        return "Nothing to remove yet; you haven't logged any glasses today."
    removed = min(glasses, data["glasses"])
    data["glasses"] = max(0, data["glasses"] - glasses)
    _publish_stat(self.room, data, data["glasses"], data["goal"])
    ...
```

`set_goal` changes the target and refuses a goal under one, so a zero goal can
never make the card announce "Goal reached!" at zero intake. The invariant holds
whatever the caller says: glasses at zero or more, goal at one or more.

## 4. Wire it up

The entrypoint builds the session on the default stack, starts the agent,
publishes an empty Stat so the card renders before anyone speaks, and greets:

```python
userdata: dict = {"glasses": 0, "goal": DEFAULT_GOAL}
session = AgentSession(
    userdata=userdata,
    stt=deepgram.STT(model="nova-3"),
    llm=openai.LLM(model="gpt-4o-mini"),
    tts=cartesia.TTS(model="sonic-2"),
    vad=ctx.proc.userdata["vad"],
    turn_detection=inference.TurnDetector(),
)

await session.start(agent=WaterCoach(ctx.room), room=ctx.room)
await ctx.connect()
_publish_stat(ctx.room, userdata, 0, DEFAULT_GOAL)
```

## 5. The eval

The behavioral test runs the agent in text mode against the real `gpt-4o-mini`
and scores the conversation with LiveKit's `evals` judges. It logs two glasses,
changes the goal, and asserts the judges pass:

```python
await session.run(user_input="I just drank two glasses of water.")
await session.run(user_input="Change my daily goal to ten glasses.")

judges = JudgeGroup(
    llm=judge_llm,
    judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
)
evaluation = await judges.evaluate(session.history)
assert evaluation.all_passed
```

## 6. Run it

```sh
cp .env.example .env
```

```sh
uv sync
```

```sh
uv run python agent.py dev
```

Open https://playground.mahimai.ca/demos/water-tracker, say how many glasses you
have had, then try to remove more than you logged and watch it floor at zero
instead of going negative.
