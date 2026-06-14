---
title: A voice hydration coach that counts your glasses out loud
summary: A minimal counter-plus-goal voice agent that logs water by voice, mirrors a live Stat card in the playground, and the mount-versus-update gotcha that keeps one publish call correct on both paths.
author: Mahimai
---

## The problem

Hitting a daily water target is a counting problem, and counting apps want
your hands. Open the app, find the button, tap, close. A voice coach skips
all of it: say "I had two glasses" and the count moves. This is a
deliberately small demo. Two state values (glasses and goal), three tools
(log_water, remove_water, set_goal) that mutate one in-memory dict, and a
single Stat card in the playground that mirrors the dict. It is for anyone
trying to reach a hydration goal hands-free while they cook, work, or move
around the house.

## Why this stack

Cookbook defaults, end to end. Deepgram Nova-3 for STT transcribes spoken
counts like "two glasses" cleanly. OpenAI gpt-4o-mini routes natural speech
to the right function tool: log, remove, or set the goal. Cartesia Sonic-2
speaks the warm encouragement and the running total back. Silero VAD is
prewarmed once per process so each session does not pay the load cost, and
the LiveKit MultilingualModel handles end-of-turn detection. Nothing here is
exotic, because the demo is about state and UI mirroring, not about pushing
the model layer.

## The interesting part

The playground envelope distinguishes a mount (first send of a component)
from an update (every later send with the same id). Rather than split that
across two publish sites, one helper tracks the mounted ids in the session
userdata dict, so a single _publish_stat call is correct for the startup
mount and every later update:

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
        "Goal reached! Great job."
        if glasses >= goal
        else f"{remaining} more to reach your goal."
    )
    publish_ui_event(
        room,
        "Stat",
        _ui_action(data, "water"),
        component_id="water",
        props={
            "label": "glasses today",
            "value": glasses,
            "of": goal,
            "caption": caption,
        },
    )
```

The mounted set lives in the same dict as glasses and goal, so all demo
state sits in one place instead of getting monkey-patched onto the room.

## What surprised me

Voice logging is exactly where miscounts happen. You say three when you
meant two, or the model hears "to" as "two." A pure additive counter would
strand the user with no way back, and the quality bar asks that a mutation
have an inverse. So remove_water is a first-class tool, not an afterthought:
it clamps removals to what is actually logged, floors the total at zero, and
refuses an empty remove with an honest message. log_water rejects anything
below one glass instead of silently subtracting, and set_goal refuses a goal
under one so a zero goal cannot make the card announce "Goal reached!" at
zero intake. The counter never leaves its own invariant.

## Run it

Talk to it at https://playground.mahimai.ca/demos/water-tracker. Say how
many glasses you have had, change your goal mid-call, and undo a miscount.
Or fork the cookbook and run the worker locally.
