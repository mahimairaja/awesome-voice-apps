---
title: A drive-thru cart that mirrors itself onto the screen
summary: A coffee-ordering voice agent that adds, removes, and modifies items mid-call and mirrors the cart and a live total onto the playground. This is the cookbook's first demo, so it exists to exercise the scaffold every later demo copies. Plus the mount-once gotcha that double-mounted every card.
author: Mahimai
---

## The problem

A coffee shop wants drive-thru orders by voice without ripping out its POS. The agent runs a ten-item cafe menu (espresso through pastries) and lets the customer add items, remove them, and attach free-form modifications mid-call: oat milk, light ice, decaf, an extra shot. As the call goes, the cart and a live total mirror onto the playground screen. It closes by taking a pickup name and reading the total back.

This is the cookbook's first demo, so its real job is not coffee. It exists to exercise the scaffold every later demo copies: three function tools, playground UI events, and room-scoped state. Get this one right and the rest inherit it.

## Why this stack

Repo defaults, end to end. Deepgram Nova-3 handles STT, OpenAI gpt-4o-mini drives tool selection (`add_item`, `remove_item`, `submit_order`) and the spoken read-back, and Cartesia Sonic-2 synthesizes the attendant's short replies. Silero VAD loads once in prewarm and caches on `proc.userdata`, so each session reuses the loaded weights instead of reloading. The LiveKit MultilingualModel turn detector decides when the customer has finished a turn before the model responds.

Nothing exotic, on purpose. This is the demo that proves the scaffold, so the providers are the boring part and the wiring is the point.

## The interesting part

The cart lives in `userdata`, and the screen has to track it. There is no separate setup hook, so the cart is published on connect and after every mutation. The catch is that the playground protocol wants exactly one mount per component id, then update for every later change:

![Mount once, then update](https://assets.mahimai.ca/drive-thru-mount-update-blog.svg)

```python
def _ui_action(room: rtc.Room, component_id: str) -> Literal["mount", "update"]:
    mounted = getattr(room, "_awesome_voice_ui_mounted", None)
    if mounted is None:
        mounted = set()
        setattr(room, "_awesome_voice_ui_mounted", mounted)

    if component_id in mounted:
        return "update"

    mounted.add(component_id)
    return "mount"


def _publish_cart(room: rtc.Room, cart: list[dict]) -> None:
    subtotal = _subtotal(cart)
    publish_ui_event(
        room,
        "Order",
        _ui_action(room, "order"),
        component_id="order",
        props={"items": [_render(item) for item in cart]},
    )
    publish_ui_event(
        room,
        "Total",
        _ui_action(room, "total"),
        component_id="total",
        props={
            "items": [
                {"label": "subtotal", "value": _fmt(subtotal)},
                {"label": "total", "value": _fmt(subtotal), "accent": True},
            ]
        },
    )
    _unmount_checkout(room)
```

`_ui_action` keeps a set of already-mounted ids on the room object: mount the first time it sees an id, update every time after. The last line clears any submitted Checkout card, because an open order and a checked-out order are mutually exclusive, so re-rendering the cart takes a stale Checkout down.

## What surprised me

The mount-versus-update decision turned out to be real application state, and I learned that the hard way. The first cut published every event with action `mount`. That reads fine until you change the cart: re-publishing `mount` on every change made the playground mount the same card again instead of updating it, so the cards double-mounted. Adding a shot drew a second order card on top of the first.

The fix is the mounted-id set in the code above. The deeper point is where that state has to live. A stateless publish helper has nowhere to remember what it has already drawn, so the memory has to go somewhere, and here that somewhere is an attribute stashed on the LiveKit room. That is what makes per-session UI idempotency work without a database or a session object: the room is the thing that lives exactly as long as the session does.

The same review caught a smaller one. The helper's parameter was named `id`, which shadows Python's builtin `id()`. It was renamed to `component_id` while still emitting the envelope key as `id`, so the wire format did not change. Easy to miss, easy to fix, and exactly the kind of thing a first demo should get right before forty others copy it.

## Run it

Talk to it at [playground.mahimai.ca/demos/drive-thru-coffee](https://playground.mahimai.ca/demos/drive-thru-coffee): order something, change your mind mid-call, and watch the cart and total update on the screen, then give a name to close. Or fork the cookbook and run the worker locally.

To see the idempotency hold, add an item, then add a second one. The order card updates in place instead of stacking a new card each time, which is the whole fix from the section above.
