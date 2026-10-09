"""Hotel lobby concierge with a face.

A talking concierge on screen recommends a restaurant near the hotel and books
a table. The voice runs as a normal STT, LLM and TTS agent; a Spatius avatar
joins the room as its own participant, takes the agent's audio and publishes
it with motion data that the browser renders as a lip-synced 3D face. The
agent also reports how well speech, lips and interruptions stay in sync.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py dev, then join the room from a frontend that uses
   the Spatius web SDK. The face is drawn in the browser, so console mode and
   plain video players show a black frame.
"""

import asyncio
import json
import logging
import random
import time
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
from livekit.plugins import openai, silero, spatius

load_dotenv()

logger = logging.getLogger(__name__)

HOTEL = "The Halden"
CONCIERGE = "Mira"
# Seconds within which the concierge going quiet counts as yielding to the caller.
INTERRUPT_WINDOW = 2.5

# A fictional neighbourhood. Times are tonight's open tables.
RESTAURANTS = [
    {
        "id": "r1",
        "name": "Osteria Lume",
        "cuisine": "Italian",
        "walk": 4,
        "price": "$$$",
        "tags": ["pasta", "wine", "vegetarian", "date night"],
        "times": ["6:30 PM", "7:45 PM", "9:00 PM"],
    },
    {
        "id": "r2",
        "name": "Kinjo",
        "cuisine": "Japanese",
        "walk": 8,
        "price": "$$$$",
        "tags": ["sushi", "omakase", "seafood", "special occasion"],
        "times": ["6:00 PM", "8:30 PM"],
    },
    {
        "id": "r3",
        "name": "Bramble & Ash",
        "cuisine": "Canadian",
        "walk": 6,
        "price": "$$$",
        "tags": ["wood-fired", "local", "seafood", "groups"],
        "times": ["7:00 PM", "8:15 PM"],
    },
    {
        "id": "r4",
        "name": "Petit Comptoir",
        "cuisine": "French",
        "walk": 3,
        "price": "$$",
        "tags": ["bistro", "wine", "quiet", "date night"],
        "times": ["6:45 PM", "7:30 PM", "9:15 PM"],
    },
    {
        "id": "r5",
        "name": "Saffron Lane",
        "cuisine": "Indian",
        "walk": 10,
        "price": "$$",
        "tags": ["curry", "vegetarian", "vegan", "spicy", "groups"],
        "times": ["6:15 PM", "8:00 PM"],
    },
    {
        "id": "r6",
        "name": "Marrow",
        "cuisine": "Steakhouse",
        "walk": 5,
        "price": "$$$$",
        "tags": ["steak", "cocktails", "business dinner"],
        "times": ["7:15 PM", "9:30 PM"],
    },
]


def initial_state() -> dict:
    """Per-call state: tonight's tables, the reservation, and mounted UI ids."""
    return {
        "restaurants": [{**r, "times": list(r["times"])} for r in RESTAURANTS],
        "reservation": None,
        "ui_mounted": set(),
    }


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
    component_id: str | None = None,
) -> None:
    envelope = {"type": "ui_event", "component": component, "action": action, "props": props or {}}
    if component_id is not None:
        envelope["id"] = component_id
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(
                json.dumps(envelope).encode(), topic="ui", reliable=True
            )
        )
    except RuntimeError:
        logger.exception("failed to schedule ui event")
        return

    def log_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception():
            logger.warning("failed to publish ui event")

    task.add_done_callback(log_failure)


def _action(mounted: set[str], component_id: str) -> Literal["mount", "update"]:
    if component_id in mounted:
        return "update"
    mounted.add(component_id)
    return "mount"


def publish_picks(room: rtc.Room, data: dict, picks: list[dict], why: str = "") -> None:
    publish_ui_event(
        room,
        "List",
        _action(data["ui_mounted"], "picks"),
        component_id="picks",
        props={
            "title": why or "tonight near the hotel",
            "items": [
                {
                    "id": r["id"],
                    "title": r["name"],
                    "subtitle": f"{r['cuisine']} · {r['walk']} min walk · {r['price']}",
                    "right": ", ".join(r["times"]) or "Full tonight",
                }
                for r in picks
            ],
        },
    )


def publish_reservation(room: rtc.Room, data: dict) -> None:
    booking = data["reservation"]
    mounted = data["ui_mounted"]
    if booking is None:
        if "reservation" in mounted:
            mounted.discard("reservation")
            publish_ui_event(room, "Card", "unmount", component_id="reservation")
        return
    publish_ui_event(
        room,
        "Card",
        _action(mounted, "reservation"),
        component_id="reservation",
        props={
            "title": f"{booking['restaurant']} at {booking['time']}",
            "subtitle": f"Party of {booking['party_size']}",
            "body": f"Guest: {booking['guest']}\nConfirmation: {booking['code']}",
            "footer": "confirmed",
        },
    )


def _match(restaurant: dict, words: str) -> bool:
    haystack = " ".join([restaurant["name"], restaurant["cuisine"], *restaurant["tags"]]).lower()
    return any(
        word in haystack for word in words.lower().replace(",", " ").split() if len(word) > 2
    )


class SyncMeter:
    """Measures the avatar from the agent's side and puts it on screen.

    - join: avatar session start to its motion track arriving in the room
    - playback: first audio frame sent to the avatar to playback starting
    - interruptions: the caller talking over the concierge, and how long it
      took from the caller's voice to the concierge going quiet
    """

    def __init__(self, room: rtc.Room) -> None:
        self.room = room
        self.stats = {
            "join_ms": None,
            "playback_ms": None,
            "playback_avg_ms": None,
            "turns": 0,
            "interruptions": 0,
            "stop_ms": None,
        }
        self._playback: list[float] = []
        self._agent_state = "initializing"
        self._talked_over_at: float | None = None
        self._mounted = False

    def attach(self, session: AgentSession) -> None:
        session.on("metrics_collected", self._on_metrics)
        session.on("agent_state_changed", self._on_agent_state)
        session.on("user_state_changed", self._on_user_state)

    def publish(self) -> None:
        action = "update" if self._mounted else "mount"
        self._mounted = True
        publish_ui_event(self.room, "Sync", action, dict(self.stats), component_id="sync")

    def _on_metrics(self, event) -> None:
        metrics = getattr(event, "metrics", event)
        if getattr(metrics, "type", "") != "avatar_metrics":
            return
        started, joined = metrics.session_started_time, metrics.avatar_joined_time
        if started and joined:
            self.stats["join_ms"] = round((joined - started) * 1000)
        elif metrics.playback_latency:
            self._playback.append(metrics.playback_latency * 1000)
            self.stats["turns"] = len(self._playback)
            self.stats["playback_ms"] = round(self._playback[-1])
            self.stats["playback_avg_ms"] = round(sum(self._playback) / len(self._playback))
        else:
            return
        self.publish()

    def _on_user_state(self, event) -> None:
        if event.new_state == "speaking" and self._agent_state == "speaking":
            self._talked_over_at = time.monotonic()

    def _on_agent_state(self, event) -> None:
        self._agent_state = event.new_state
        if event.old_state != "speaking" or self._talked_over_at is None:
            return
        stopped_after = time.monotonic() - self._talked_over_at
        self._talked_over_at = None
        # A reply that simply ended while the caller spoke is not an interruption.
        if stopped_after <= INTERRUPT_WINDOW:
            self.stats["interruptions"] += 1
            self.stats["stop_ms"] = round(stopped_after * 1000)
            self.publish()


class HotelConcierge(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                f"You are {CONCIERGE}, the evening concierge at {HOTEL}, a hotel downtown. "
                "You appear on screen as a video avatar, so speak like a warm person at a "
                "lobby desk: short sentences, no lists read aloud, no markdown or emojis. "
                "Help the guest choose a restaurant for tonight and book a table. "
                "Call recommend as soon as they say what they feel like, so the options "
                "appear on screen, then suggest one or two with a reason. "
                "Before booking, confirm the restaurant, time, party size and a name, then "
                "call book_table with the restaurant id. Read the confirmation code back "
                "slowly. If they change their mind, call cancel_table, then book again. "
                "Only offer times the tools return. If the guest talks over you, stop and "
                "answer what they just said."
            ),
        )
        self.room = room

    @function_tool()
    async def recommend(
        self,
        context: RunContext[dict],
        craving: str | None = None,
        max_walk_minutes: int | None = None,
    ) -> str:
        """Find restaurants near the hotel with a table tonight.

        Args:
            craving: what the guest feels like, for example "sushi", "vegetarian",
                "quiet date night" or "steak". Omit to list everything.
            max_walk_minutes: only include places within this walk.
        """
        data = context.userdata
        picks = [r for r in data["restaurants"] if r["times"]]
        if craving:
            matched = [r for r in picks if _match(r, craving)]
            picks = matched or picks
            exact = bool(matched)
        else:
            exact = True
        if max_walk_minutes:
            picks = [r for r in picks if r["walk"] <= max_walk_minutes] or picks
        publish_picks(self.room, data, picks, f"for {craving}" if craving and exact else "")
        lines = "; ".join(
            f"{r['id']}: {r['name']}, {r['cuisine']}, {r['walk']} min walk, {r['price']}, "
            f"tables at {', '.join(r['times'])}"
            for r in picks
        )
        prefix = "" if exact else f"Nothing matches {craving} exactly. "
        return f"{prefix}Open tonight: {lines}"

    @function_tool()
    async def book_table(
        self,
        context: RunContext[dict],
        restaurant_id: str,
        time: str,
        party_size: int,
        guest_name: str,
    ) -> str:
        """Book a table tonight. Use only after the guest confirms every detail.

        Args:
            restaurant_id: the id from recommend, like "r1".
            time: one of that restaurant's open times, exactly as returned, like "7:45 PM".
            party_size: number of guests, 1 to 8.
            guest_name: the name for the reservation.
        """
        data = context.userdata
        if data["reservation"]:
            booked = data["reservation"]
            return (
                f"There is already a table at {booked['restaurant']} at {booked['time']}. "
                "Cancel it first if they want a different one."
            )
        restaurant = next((r for r in data["restaurants"] if r["id"] == restaurant_id), None)
        if restaurant is None:
            return f"Unknown restaurant {restaurant_id}. Call recommend and use an id it returns."
        wanted = time.strip().upper().replace(".", "")
        slot = next((t for t in restaurant["times"] if t.upper() == wanted), None)
        if slot is None:
            return (
                f"{restaurant['name']} has no table at {time}. "
                f"Open times: {', '.join(restaurant['times']) or 'none tonight'}."
            )
        if not 1 <= party_size <= 8:
            return "Parties of 1 to 8 only. For larger groups the hotel arranges a private room."
        name = guest_name.strip()[:40]
        if not name:
            return "Ask for a name for the reservation."
        restaurant["times"].remove(slot)
        data["reservation"] = {
            "restaurant_id": restaurant["id"],
            "restaurant": restaurant["name"],
            "time": slot,
            "party_size": party_size,
            "guest": name,
            "code": f"HL-{random.randint(1000, 9999)}",
        }
        publish_reservation(self.room, data)
        publish_picks(self.room, data, [r for r in data["restaurants"] if r["times"]])
        booked = data["reservation"]
        return (
            f"Booked {booked['restaurant']} at {booked['time']} for {party_size} under {name}. "
            f"Confirmation {booked['code']}. This is a simulation; no real table is held."
        )

    @function_tool()
    async def cancel_table(self, context: RunContext[dict]) -> str:
        """Cancel tonight's reservation and free the table."""
        data = context.userdata
        booked = data["reservation"]
        if booked is None:
            return "There is no reservation to cancel."
        restaurant = next(r for r in data["restaurants"] if r["id"] == booked["restaurant_id"])
        original = next(r for r in RESTAURANTS if r["id"] == restaurant["id"])["times"]
        restaurant["times"] = [
            t for t in original if t in restaurant["times"] or t == booked["time"]
        ]
        data["reservation"] = None
        publish_reservation(self.room, data)
        publish_picks(self.room, data, [r for r in data["restaurants"] if r["times"]])
        return f"Cancelled {booked['restaurant']} at {booked['time']}."


def avatar_session() -> spatius.AvatarSession:
    # Reads SPATIUS_API_KEY, SPATIUS_APP_ID and SPATIUS_AVATAR_ID from the environment.
    return spatius.AvatarSession(avatar_participant_name=CONCIERGE)


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="hotel-concierge")
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(
        userdata=initial_state(),
        stt=openai.STT(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(voice="coral"),
        vad=ctx.proc.userdata["vad"],
    )
    await ctx.connect()
    meter = SyncMeter(ctx.room)
    meter.attach(session)
    # The avatar joins as a second participant. Start it first so the greeting
    # has a face; the session's audio then goes to the avatar, not the room.
    avatar = avatar_session()
    await avatar.start(session, room=ctx.room)
    await avatar.wait_for_join()
    agent = HotelConcierge(ctx.room)
    await session.start(agent=agent, room=ctx.room)
    publish_picks(ctx.room, session.userdata, session.userdata["restaurants"])
    meter.publish()
    await session.generate_reply(
        instructions=f"Welcome the guest to {HOTEL} and ask what they feel like eating tonight."
    )


if __name__ == "__main__":
    cli.run_app(server)
