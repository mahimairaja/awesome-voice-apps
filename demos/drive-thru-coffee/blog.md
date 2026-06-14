---
title: A drive-thru cart that mirrors itself onto the screen
summary: A coffee-ordering voice agent that adds, removes, and modifies items mid-call, mirrors the cart and a live total onto the playground, and the mount-once gotcha that double-mounted every card.
author: Mahimai
---

## The problem

A coffee shop wants drive-thru orders by voice without ripping out its
POS. The agent runs a ten-item cafe menu (espresso through pastries),
lets the customer add and remove items and attach free-form
modifications (oat milk, light ice, decaf, an extra shot) mid-call,
mirrors the cart and a live total onto the playground screen as the call
goes, then closes by taking a pickup name and reading back the total.
This is the cookbook's first demo. It exists to exercise the scaffold
(three function tools, playground UI events, room-scoped state) that
every later demo copies.

## Why this stack

Repo defaults, end to end. Deepgram Nova-3 handles STT, OpenAI
gpt-4o-mini drives tool selection (add_item, remove_item, submit_order)
and the spoken read-back, and Cartesia Sonic-2 synthesizes the
attendant's short plain-text replies. Silero VAD loads once in prewarm
and caches on proc.userdata so each session reuses the loaded weights
instead of reloading. LiveKit MultilingualModel decides when the customer
has finished a turn before the model responds. Nothing exotic, because
the point is the scaffold, not the providers.

## The interesting part

The cart lives in userdata; the screen has to track it. There is no
separate setup hook, so the cart is published on connect and after every
mutation:

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
```

The playground protocol wants exactly one mount per component id, then
update for every later change. `_ui_action` stashes a set of
already-mounted ids on the room object and returns mount the first time
it sees an id, update thereafter.

## What surprised me

The first cut published every event with action mount. Re-publishing
mount on every cart change made the playground mount the same card
repeatedly instead of updating it, so the cards double-mounted. That
exact bug is what review commit 6a2c139 was written to fix. The lesson
the code embodies: in a stateless publish helper, the mount-versus-update
decision is real application state, and it has to live somewhere. Here
that somewhere is an attribute monkey-patched onto the LiveKit room,
which is what makes per-session UI idempotency work without a database or
a session object. The same review caught a second defect: the helper's
`id` parameter shadowed Python's builtin `id()`, so it was renamed to
`component_id` while still emitting the envelope key as `id`.

## Run it

Talk to it at https://playground.mahimai.ca/demos/drive-thru-coffee.
Order, change your mind mid-call, watch the cart and total update on the
screen, then give a name to close. Or fork the cookbook and run the
worker locally.
