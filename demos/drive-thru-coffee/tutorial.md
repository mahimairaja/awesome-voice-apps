---
title: How to build a drive-thru coffee voice agent
summary: Build a drive-thru attendant that takes an order, modifies items mid-flow, totals the cart, and mirrors it to the screen, from an empty folder to a running worker, on Deepgram, OpenAI, and Cartesia.
author: Mahimai
github: mahimairaja
---

## What you will build

A drive-thru coffee attendant. The customer orders by voice, changes items
mid-flow, and the cart plus a live total mirror onto the playground screen. It
closes by taking a pickup name. This is the cookbook's first demo, so its real
job is the scaffold every later demo copies: function tools, playground UI
events, and room-scoped state. The finished demo is `drive-thru-coffee`.

You need three provider keys (Deepgram, OpenAI, Cartesia) and three LiveKit
values. And `uv`.

## 1. Scaffold

Pin Python to `3.11`. `pyproject.toml` pulls the three repo-default plugins:

```toml
[project]
name = "drive-thru-coffee"
version = "0.1.0"
description = "Takes a coffee order, modifies items mid-flow, totals the cart."
requires-python = ">=3.11"
dependencies = [
    "livekit-agents[deepgram,openai,cartesia]>=1.6,<2.0",
    "python-dotenv>=1.0",
]
```

`.env.example` lists the six keys:

```
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
OPENAI_API_KEY=
DEEPGRAM_API_KEY=
CARTESIA_API_KEY=
```

## 2. The menu and the money

The menu is a dict keyed by a stable `item_key`, with prices in integer cents.
Formatting happens only at the edge, so a total never picks up a float rounding
bug:

```python
MENU = {
    "espresso": {"label": "espresso", "price": 350},
    "americano": {"label": "americano", "price": 425},
    "latte": {"label": "latte", "price": 575},
    ...
}


def _fmt(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def _subtotal(cart: list[dict]) -> int:
    return sum(item["price"] for item in cart)
```

> [!TIP]
> Keep money in integer cents and format only when you display it. A float total
> is a rounding bug waiting for the wrong order.

## 3. The cart and its three tools

The cart is one list in `userdata`. `add_item` looks the key up in the menu,
appends the item with any modifications, and re-publishes the cart:

```python
@function_tool()
async def add_item(
    self,
    context: RunContext[dict],
    item_key: str,
    modifications: list[str] | None = None,
) -> str:
    menu_item = MENU.get(item_key)
    if menu_item is None:
        return "I do not have that item."

    item = {
        "key": item_key,
        "label": menu_item["label"],
        "price": menu_item["price"],
        "modifications": modifications or [],
    }
    cart = context.userdata["cart"]
    cart.append(item)
    _publish_cart(self.room, cart)
    ...
```

`remove_item` is the inverse operation, popping by index and re-publishing, so
the cart and the screen never drift:

```python
@function_tool()
async def remove_item(self, context: RunContext[dict], index: int) -> str:
    cart = context.userdata["cart"]
    if index < 0 or index >= len(cart):
        return "Nothing at that position."

    item = cart.pop(index)
    _publish_cart(self.room, cart)
    return f"Removed {item['label']}."
```

`submit_order` refuses an empty cart, then publishes a Checkout card with the
pickup name.

## 4. Mirror it on screen (mount once, then update)

The playground protocol wants exactly one `mount` per component id, then
`update` for every later change. A helper tracks which ids are already up by
stashing a set on the room object:

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
> the first.

The room is the right place for that state because it lives exactly as long as
the session does, so per-session UI idempotency works without a database. Every
cart change publishes the Order and Total, then clears any submitted Checkout,
because an open order and a checked-out order are mutually exclusive:

```python
def _unmount_checkout(room: rtc.Room) -> None:
    mounted = getattr(room, "_awesome_voice_ui_mounted", None)
    if not mounted or "checkout" not in mounted:
        return
    mounted.discard("checkout")
    publish_ui_event(room, "Checkout", "unmount", component_id="checkout")
```

The full menu renders as a static List the whole call, so the instructions tell
the model to point at the screen rather than read ten items aloud.

## 5. The eval

The behavioral test runs the agent in text mode against the real `gpt-4o-mini`
and scores the conversation with LiveKit's `evals` judges. It orders a drink
with a modification, gives a name, and asserts the judges pass:

```python
await session.run(user_input="A large iced latte with oat milk, please.")
await session.run(user_input="That is everything, and the name is Sam.")

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

Open https://playground.mahimai.ca/demos/drive-thru-coffee, order something,
then add a second item and watch the order card update in place instead of
stacking a new one.
