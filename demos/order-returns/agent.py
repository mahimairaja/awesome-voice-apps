"""Order returns voice agent.

Start a return by voice: pull up an order, choose which items to send back
and why, then get a refund summary on screen.

Run it:
1. Copy templates/livekit-base/.env.example to .env and fill OPENAI_API_KEY
   plus your three LiveKit values.
2. uv sync
3. uv run --no-project python agent.py download-files
4. uv run --no-project python agent.py dev, then open
   https://playground.mahimai.ca/demos/order-returns.
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
from livekit.plugins import openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()

logger = logging.getLogger(__name__)

ORDERS: dict[str, dict] = {
    "ORD-001": {
        "items": [
            {"id": 0, "name": "Blue hoodie (size M)", "price": 4999},
            {"id": 1, "name": "White sneakers (size 10)", "price": 8999},
        ]
    },
    "ORD-002": {
        "items": [
            {"id": 0, "name": "Wireless headphones", "price": 12999},
            {"id": 1, "name": "Phone case", "price": 1499},
        ]
    },
    "ORD-003": {
        "items": [
            {"id": 0, "name": "Yoga mat", "price": 3499},
            {"id": 1, "name": "Water bottle", "price": 2499},
            {"id": 2, "name": "Resistance bands", "price": 1999},
        ]
    },
}

RETURN_REASONS = [
    "changed mind",
    "wrong size",
    "defective or damaged",
    "not as described",
    "arrived too late",
]


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
    component_id: str | None = None,
) -> None:
    envelope = {
        "type": "ui_event",
        "component": component,
        "action": action,
        "props": props or {},
    }
    if component_id is not None:
        envelope["id"] = component_id

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

    def log_publish_failure(t: asyncio.Task[None]) -> None:
        try:
            t.result()
        except Exception:
            logger.exception("failed to publish playground ui event")

    task.add_done_callback(log_publish_failure)


def _fmt(cents: int) -> str:
    return f"${cents // 100}.{cents % 100:02d}"


def _ui_action(room: rtc.Room, component_id: str) -> Literal["mount", "update"]:
    mounted = getattr(room, "_awesome_voice_ui_mounted", None)
    if mounted is None:
        mounted = set()
        setattr(room, "_awesome_voice_ui_mounted", mounted)
    if component_id in mounted:
        return "update"
    mounted.add(component_id)
    return "mount"


def _publish_return_cart(room: rtc.Room, cart: list[dict]) -> None:
    items = [
        {
            "title": item["name"],
            "subtitle": item["reason"],
            "right": _fmt(item["price"]),
        }
        for item in cart
    ]
    publish_ui_event(
        room,
        "List",
        _ui_action(room, "return-cart"),
        component_id="return-cart",
        props={"title": "return cart", "items": items},
    )


class ReturnsAgent(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                "You are a friendly returns agent for an online retail store. "
                "Help the caller start a return: look up their order by ID, "
                "add items they want to return with a reason, remove items if "
                "they change their mind, then submit the return. Keep replies "
                "short, plain text, no markdown or emojis. "
                "Valid return reasons: " + ", ".join(RETURN_REASONS) + ". "
                "Available orders for demo: ORD-001, ORD-002, ORD-003."
            ),
        )
        self.room = room

    @function_tool()
    async def look_up_order(
        self,
        context: RunContext[dict],
        order_id: str,
    ) -> str:
        """Look up an order by ID and list its items."""
        order = ORDERS.get(order_id.strip().upper())
        if order is None:
            return f"No order found with ID {order_id}."

        context.userdata["order"] = order
        context.userdata["order_id"] = order_id.strip().upper()

        lines = [f"Order {order_id.upper()} has {len(order['items'])} item(s):"]
        for item in order["items"]:
            lines.append(f"  {item['id']}: {item['name']}, {_fmt(item['price'])}")
        return "\n".join(lines)

    @function_tool()
    async def add_return_item(
        self,
        context: RunContext[dict],
        item_index: int,
        reason: str,
    ) -> str:
        """Add one item from the order to the return cart.

        item_index is the item's zero-based index from look_up_order.
        reason must be one of the valid return reasons.
        """
        order = context.userdata.get("order")
        if order is None:
            return "Please look up an order first."

        items = order["items"]
        if item_index < 0 or item_index >= len(items):
            return "No item at that position."

        if reason.strip().lower() not in RETURN_REASONS:
            return (
                "That is not a valid return reason. Valid reasons: "
                + ", ".join(RETURN_REASONS)
                + "."
            )

        cart: list[dict] = context.userdata["cart"]
        item = items[item_index]

        if any(c["id"] == item["id"] for c in cart):
            return f"{item['name']} is already in the return cart."

        cart.append(
            {
                "id": item["id"],
                "name": item["name"],
                "price": item["price"],
                "reason": reason,
            }
        )
        _publish_return_cart(self.room, cart)
        return f"Added {item['name']} to the return. Reason: {reason}."

    @function_tool()
    async def remove_return_item(
        self,
        context: RunContext[dict],
        item_index: int,
    ) -> str:
        """Remove an item from the return cart by its zero-based position."""
        cart: list[dict] = context.userdata["cart"]
        if item_index < 0 or item_index >= len(cart):
            return "Nothing at that position in the return cart."

        item = cart.pop(item_index)
        _publish_return_cart(self.room, cart)
        return f"Removed {item['name']} from the return."

    @function_tool()
    async def submit_return(self, context: RunContext[dict]) -> str:
        """Submit the return and generate a refund summary."""
        cart: list[dict] = context.userdata["cart"]
        if not cart:
            return "The return cart is empty. Please add at least one item first."

        order_id = context.userdata.get("order_id", "your order")
        total = sum(item["price"] for item in cart)
        item_summary = "; ".join(
            f"{item['name']} ({_fmt(item['price'])})" for item in cart
        )
        body = (
            f"Items: {item_summary}. "
            f"Refund of {_fmt(total)} to your original payment method "
            "within 5 to 7 business days."
        )

        publish_ui_event(
            self.room,
            "Card",
            _ui_action(self.room, "refund-summary"),
            component_id="refund-summary",
            props={
                "title": "return confirmed",
                "subtitle": order_id,
                "body": body,
                "accent": True,
            },
        )
        return (
            f"Return submitted for {order_id}. "
            f"Refund of {_fmt(total)} on its way in 5 to 7 business days."
        )


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="order-returns")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    userdata: dict = {"cart": [], "order": None, "order_id": None}
    session = AgentSession(
        userdata=userdata,
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )

    await session.start(agent=ReturnsAgent(ctx.room), room=ctx.room)
    await ctx.connect()
    await session.generate_reply(
        instructions="Greet the caller and ask for their order ID to get started."
    )


if __name__ == "__main__":
    cli.run_app(server)
