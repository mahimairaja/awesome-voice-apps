---
title: How to build a drive-thru coffee voice agent
summary: A drive-thru attendant that takes a coffee order, modifies items mid-flow, totals the cart, and closes with a pickup name, on Deepgram, OpenAI, and Cartesia.
author: Mahimai
github: mahimairaja
---

A drive-thru order is a small state machine: add items, change them mid-flow,
total the cart, and close with a name on the cup. This agent runs that whole
loop by voice and mirrors the cart to the screen as it goes. It is the
cookbook's first demo, so it also sets the UI scaffold every later demo copies.

The stack is Deepgram Nova-3 for STT, `gpt-4o-mini` for the tools, and Cartesia
Sonic-2 for the voice. Turn-taking is LiveKit's inference VAD and turn detector.

Money is integer cents everywhere, formatted only at the edge, so a total never
picks up a floating-point rounding bug:

```python
def _fmt(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"
```

The cart is one list in `userdata`. `add_item` looks the item up in the menu,
appends it, and re-publishes the Order and Total; `remove_item` is its exact
inverse, so the cart and the screen never drift:

```python
cart = context.userdata["cart"]
cart.append(item)
_publish_cart(self.room, cart)
```

`submit_order` closes the loop: it refuses an empty cart, takes a pickup name,
and publishes a Checkout card with the total, so the customer sees the order
land before they pull forward.

The scaffold worth copying is UI idempotency. The playground wants exactly one
mount per component id, then update for every later change, so a helper tracks
which ids are already up on the room object:

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
```

> [!WARNING]
> Publish every change as `mount` and the playground stacks a new card each time
> instead of updating in place. Adding a shot draws a second order card on top of
> the first. Mount once per id, then update.

Build it from an empty folder in the full walkthrough, or talk to the finished
agent at https://playground.mahimai.ca/demos/drive-thru-coffee.
