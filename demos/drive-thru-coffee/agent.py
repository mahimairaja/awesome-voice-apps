"""Drive-thru coffee voice agent.

Takes a coffee order, lets the customer modify items mid-flow, updates
the cart on the playground screen, computes totals, and closes with a
pickup name.

Run it:
1. Copy templates/livekit-base/.env.example to .env and fill the six keys.
2. Run uv venv, then uv pip install -r requirements.txt.
3. Run uv run --no-project python agent.py download-files.
4. Run uv run --no-project python agent.py dev, then open
   https://playground.mahimai.ca/demos/drive-thru-coffee.

Recording: coming after the demo is recorded.
"""

import asyncio
import json
import logging
from typing import Literal

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
)
from livekit.plugins import cartesia, deepgram, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()

logger = logging.getLogger(__name__)

MENU = {
    "espresso": {"label": "espresso", "price": 350},
    "americano": {"label": "americano", "price": 425},
    "latte": {"label": "latte", "price": 575},
    "cappuccino": {"label": "cappuccino", "price": 575},
    "cold_brew": {"label": "cold brew", "price": 525},
    "iced_latte": {"label": "iced latte", "price": 625},
    "chocolate_croissant": {"label": "chocolate croissant", "price": 425},
    "blueberry_muffin": {"label": "blueberry muffin", "price": 375},
    "banana_bread": {"label": "banana bread", "price": 350},
    "oatmeal": {"label": "oatmeal", "price": 495},
}


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
    id: str | None = None,
) -> None:
    """Publish a playground UI event; see protocol.ts and demo component registries."""
    envelope = {
        "type": "ui_event",
        "component": component,
        "action": action,
        "props": props or {},
    }
    if id is not None:
        envelope["id"] = id

    try:
        payload = json.dumps(envelope).encode("utf-8")
    except (TypeError, ValueError):
        logger.exception("failed to encode playground ui event")
        return

    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(
                payload,
                topic="ui",
                reliable=True,
            )
        )
    except RuntimeError:
        logger.exception("failed to schedule playground ui event")
        return

    def log_publish_failure(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("failed to publish playground ui event")

    task.add_done_callback(log_publish_failure)


def _fmt(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def _menu_prompt() -> str:
    return ", ".join(
        f"{key}: {item['label']} {_fmt(item['price'])}"
        for key, item in MENU.items()
    )


def _subtotal(cart: list[dict]) -> int:
    return sum(item["price"] for item in cart)


def _render(item: dict) -> dict:
    rendered = {
        "label": item["label"],
        "value": _fmt(item["price"]),
    }
    if item["modifications"]:
        rendered["modifications"] = item["modifications"]
    return rendered


def _publish_cart(room: rtc.Room, cart: list[dict]) -> None:
    subtotal = _subtotal(cart)
    publish_ui_event(
        room,
        "Order",
        "mount",
        id="order",
        props={"items": [_render(item) for item in cart]},
    )
    publish_ui_event(
        room,
        "Total",
        "mount",
        id="total",
        props={
            "items": [
                {"label": "subtotal", "value": _fmt(subtotal)},
                {"label": "total", "value": _fmt(subtotal), "accent": True},
            ]
        },
    )


class DriveThruAttendant(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                "You are a friendly drive-thru voice attendant for a coffee shop. "
                "Take the customer's order, suggest add-ons sparingly, only one "
                "if the order is small, confirm modifications, and run the "
                "running total in your head as items are added. When the "
                "customer says they are done, read the order and total back, "
                "take a name for the cup, and end politely. Keep replies short. "
                "Plain text only. No markdown, no emojis. The cart updates on "
                "the customer's screen as you add items; rely on it being "
                "visible. After submit_order succeeds, wrap up the call within "
                "a couple of turns. Use add_item with these exact item keys: "
                f"{_menu_prompt()}."
            ),
        )
        self.room = room

    @function_tool()
    async def add_item(
        self,
        context: RunContext[dict],
        item_key: str,
        modifications: list[str] | None = None,
    ) -> str:
        """Add one menu item to the current order.

        Use the item_key from the menu. Call this after the customer has
        named an item and any modifications they want.
        """
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

        if item["modifications"]:
            mods = ", ".join(item["modifications"])
            return f"Added one {item['label']} with {mods}."
        return f"Added one {item['label']}."

    @function_tool()
    async def remove_item(self, context: RunContext[dict], index: int) -> str:
        """Remove an item by its zero-based position in the order."""
        cart = context.userdata["cart"]
        if index < 0 or index >= len(cart):
            return "Nothing at that position."

        item = cart.pop(index)
        _publish_cart(self.room, cart)
        return f"Removed {item['label']}."

    @function_tool()
    async def submit_order(self, context: RunContext[dict], customer_name: str) -> str:
        """Submit the current order after the customer provides a cup name."""
        cart = context.userdata["cart"]
        total = _subtotal(cart)
        name = customer_name.strip() or "friend"
        publish_ui_event(
            self.room,
            "Checkout",
            "mount",
            id="checkout",
            props={
                "title": "ready when you are",
                "buttons": [
                    {"label": f"thanks {name}", "primary": True},
                ],
            },
        )
        return f"Your order for {name} is in. Total {_fmt(total)}. Pull up to the next window."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="drive-thru-coffee")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    userdata = {"cart": []}
    session = AgentSession(
        userdata=userdata,
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-2"),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )

    await session.start(agent=DriveThruAttendant(ctx.room), room=ctx.room)
    await ctx.connect()
    _publish_cart(ctx.room, userdata["cart"])
    await session.generate_reply(
        instructions="Greet the customer and ask what they would like."
    )


if __name__ == "__main__":
    cli.run_app(server)
