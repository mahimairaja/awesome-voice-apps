"""water-tracker: voice hydration coach.

Logs glasses of water by voice and tracks progress toward a daily goal.

Run it:
1. Copy templates/livekit-base/.env.example to .env and fill the six keys.
2. uv sync
3. uv run --no-project python agent.py dev, then open
   https://playground.mahimai.ca/demos/water-tracker.
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
    inference,
)
from livekit.plugins import cartesia, deepgram, openai

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_GOAL = 8


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
        props={
            "label": "glasses today",
            "value": glasses,
            "of": goal,
            "caption": caption,
        },
    )


class WaterCoach(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                "You are a friendly hydration coach. Help the user track their daily water intake. "
                "When they say they drank water, call log_water with the number of glasses. "
                "When they want to undo or correct a miscount, call remove_water with the number of glasses. "
                "When they want to change their daily goal, call set_goal with the new target. "
                "Encourage them warmly to stay hydrated. "
                "Keep replies short, plain text, no markdown or emojis."
            ),
        )
        self.room = room

    @function_tool()
    async def log_water(
        self,
        context: RunContext[dict],
        glasses: int = 1,
    ) -> str:
        """Log glasses of water drunk and update the progress tracker.

        Call this whenever the user says they drank water.
        glasses: number of glasses to add (default 1).
        """
        if glasses < 1:
            return "I can only log one or more glasses; say 'undo' if you miscounted."
        data = context.userdata
        data["glasses"] += glasses
        _publish_stat(self.room, data, data["glasses"], data["goal"])
        remaining = max(0, data["goal"] - data["glasses"])
        if data["glasses"] > data["goal"]:
            return (
                f"Logged {glasses} glass(es). You're already past your goal at "
                f"{data['glasses']}/{data['goal']}."
            )
        if data["glasses"] == data["goal"]:
            return f"Logged {glasses} glass(es). Total: {data['glasses']}/{data['goal']}. Goal reached!"
        return (
            f"Logged {glasses} glass(es). Total: {data['glasses']}/{data['goal']}. "
            f"{remaining} more to go."
        )

    @function_tool()
    async def remove_water(
        self,
        context: RunContext[dict],
        glasses: int = 1,
    ) -> str:
        """Remove glasses of water logged in error and update the progress tracker.

        Call this when the user wants to undo or correct a miscount.
        glasses: number of glasses to remove (default 1).
        """
        if glasses < 1:
            return "I can only remove one or more glasses; tell me how many to undo."
        data = context.userdata
        if data["glasses"] == 0:
            return "Nothing to remove yet; you haven't logged any glasses today."
        removed = min(glasses, data["glasses"])
        data["glasses"] = max(0, data["glasses"] - glasses)
        _publish_stat(self.room, data, data["glasses"], data["goal"])
        remaining = max(0, data["goal"] - data["glasses"])
        return (
            f"Removed {removed} glass(es). Total: {data['glasses']}/{data['goal']}. "
            f"{remaining} more to go."
        )

    @function_tool()
    async def set_goal(
        self,
        context: RunContext[dict],
        goal: int,
    ) -> str:
        """Change the daily water goal.

        Call this when the user wants to set or change their daily target.
        goal: new target in glasses.
        """
        if goal < 1:
            return "A daily goal needs to be at least one glass."
        data = context.userdata
        data["goal"] = goal
        _publish_stat(self.room, data, data["glasses"], data["goal"])
        return f"Goal updated to {goal} glasses per day."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = inference.VAD()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="water-tracker")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

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
    await session.generate_reply(
        instructions=(
            f"Greet the user. Tell them their goal is {DEFAULT_GOAL} glasses today "
            "and ask how many they have had so far."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
