---
title: A voice hydration coach that counts your glasses out loud
summary: The smallest demo in the cookbook: a voice agent that logs water, mirrors a live Stat card on screen, and treats undo as a first-class problem, because voice miscounts and every count needs a way back.
author: Mahimai
---

## The problem

Hitting a daily water target is a counting problem, and counting apps want your hands: open the app, find the button, tap, close. A voice coach skips all of that. Say "I had two glasses" while you are cooking and the count moves.

This is the smallest demo in the cookbook, on purpose. Two values (glasses and a goal), three tools that mutate one in-memory dict, and a single Stat card on screen that mirrors it. Something this small has nowhere to hide, which makes it a good place to get one thing right: what happens when the count is wrong.

## Why this stack

Cookbook defaults, end to end. Deepgram Nova-3 turns spoken counts like "two glasses" into text, OpenAI gpt-4o-mini routes that to the right tool (log, remove, or set the goal), and Cartesia Sonic-2 speaks the running total back with a bit of encouragement. Silero VAD is prewarmed once per process so a session never pays the load cost, and the LiveKit MultilingualModel handles end-of-turn detection.

The providers are boring on purpose. This demo is about state and what guards it, not about the model layer.

## The interesting part

The drive-thru demo set up a rule the playground enforces: the first send of a component is a mount, every later send with the same id is an update, so the agent has to track which ids it has already mounted. The water tracker reuses that rule with one cleanup. Instead of stashing the mounted set on the room object, it keeps it in the same session dict as everything else, so a single `_publish_stat` call is correct for the startup mount and every update after:

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

The mounted set lives in the same dict as glasses and goal, so all of the demo's state sits in one place instead of being monkey-patched onto the room. This is the version of the pattern I would copy into the next demo.

## What surprised me

The interesting problem in a counter is not adding. It is undoing.

Voice logging is exactly where miscounts happen: you say three when you meant two, or the model hears "to" as "two." A counter that only goes up would strand you there, so every mutation in this demo has an inverse and a guard.

![Guarded, reversible counter](https://assets.mahimai.ca/water-tracker-inverse-guard-blog.svg)

`remove_water` is a first-class tool, not an afterthought. It clamps a removal to what is actually logged, floors the total at zero, and refuses an empty remove with an honest message instead of silently doing nothing. `log_water` rejects anything below one glass rather than quietly subtracting, and `set_goal` refuses a goal under one, so a zero goal can never make the card announce "Goal reached!" at zero intake. Whatever the caller says, the count stays valid: glasses at zero or more, goal at one or more.

## Run it

Talk to it at [playground.mahimai.ca/demos/water-tracker](https://playground.mahimai.ca/demos/water-tracker): say how many glasses you have had and watch the Stat card move, then change your goal mid-call. Or fork the cookbook and run the worker locally.

To see the guard work, log a few glasses, then try to remove more than you logged. The agent floors the count at zero and tells you, instead of going negative.
