"""phone-tree-router: say what you need instead of pressing 1.

A caller to Northvale, a fictional telecom, says what they need in their own
words and lands in the right queue. A router call scores every queue on each
caller turn. Code, not the model, decides: route at 75% or above, otherwise ask one clarifying question, and
never more than two. The agent hands over a one-line summary so the caller does
not repeat themselves, and the screen races a classic press-1 menu to the same
queue.

Stack: Deepgram Nova-3 STT, OpenAI gpt-4o-mini (conversation and router),
Cartesia Sonic 3 TTS.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
import json
import logging
import time

import routing
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    ChatMessage,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
)
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger(__name__)

ROUTER_MODEL = "gpt-4o-mini"
ROUTER_TIMEOUT = 2.5

INSTRUCTIONS = (
    f"You answer the {routing.BRAND} support line. Your job is to get the caller to the "
    "right team fast, not to solve the problem yourself. Each caller turn comes with a "
    "router note. Follow it exactly: when it says to route, call transfer with a "
    "one-sentence summary of what the caller needs, in their words. When it says it is "
    "unsure between two teams, ask one short question that tells them apart, in plain "
    "words, without naming departments or listing options. When there is no request "
    "yet, ask what they are calling about. Never read out a menu, never ask for an "
    "account number, and never transfer on your own judgement. Keep every reply to one "
    "short sentence, plain text, no markdown, no emojis."
)


def publish_ui_event(room: rtc.Room, component: str, props: dict) -> None:
    envelope = {"type": "ui_event", "component": component, "action": "update", "props": props}
    payload = json.dumps(envelope).encode("utf-8")
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except RuntimeError:
        logger.exception("failed to schedule playground ui event")
        return
    task.add_done_callback(lambda t: t.cancelled() or t.exception())


def publish_router(room: rtc.Room, state: dict) -> None:
    publish_ui_event(room, "Router", routing.snapshot(state))


class PhoneTreeRouter(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self._router = None
        self._speaking_since: float | None = None

    def meter_router(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Bill router calls to this call; the hosted playground overrides it."""

    def router_client(self):
        # One client per call: thread-executor jobs each run their own event loop.
        if self._router is None:
            from openai import AsyncOpenAI

            self._router = AsyncOpenAI(max_retries=0, timeout=ROUTER_TIMEOUT)
        return self._router

    async def classify(self, conversation: str) -> dict[str, float]:
        """Score every queue in one small structured-output call."""
        result = await self.router_client().chat.completions.create(
            model=ROUTER_MODEL,
            messages=[
                {"role": "system", "content": routing.router_prompt()},
                {"role": "user", "content": conversation},
            ],
            max_tokens=80,
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "queue_scores",
                    "strict": True,
                    "schema": routing.ROUTER_SCHEMA,
                },
            },
            store=False,
        )
        if result.usage:
            self.meter_router(result.usage.prompt_tokens, result.usage.completion_tokens)
        return routing.read_scores(json.loads(result.choices[0].message.content or "{}"))

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        state = self.session.userdata
        heard = (new_message.text_content or "").strip()
        if not heard:
            return
        if state["routed"] is not None:
            # A new request after a transfer: both clocks restart from when they spoke.
            routing.new_round(state, self._speaking_since or time.time())
        routing.hear(state, "caller", heard)
        started = time.perf_counter()
        try:
            probs = await asyncio.wait_for(self.classify(routing.transcript(state)), ROUTER_TIMEOUT)
        except Exception:  # noqa: BLE001 - a router outage must not drop the call
            logger.warning("router unavailable this turn")
            decision = routing.record_error(state, heard)
        else:
            ms = int((time.perf_counter() - started) * 1000)
            decision = routing.record(state, heard, probs, ms)
        publish_router(self.room, state)
        turn_ctx.add_message(role="system", content=routing.instruction(decision))

    @function_tool()
    async def transfer(self, context: RunContext[dict], summary: str) -> str:
        """Transfer the caller to the queue the router chose.

        summary: one sentence on what the caller needs, so the team does not ask again.
        """
        result = routing.transfer(context.userdata, summary, time.time())
        publish_router(self.room, context.userdata)
        return result

    def watch(self) -> None:
        """Track what the agent says (for the router) and when the caller starts talking."""

        def added(event) -> None:
            item = event.item
            text = getattr(item, "text_content", None)
            if getattr(item, "role", None) == "assistant" and text:
                routing.hear(self.session.userdata, "agent", text)

        def user_state(event) -> None:
            if event.new_state == "speaking":
                self._speaking_since = time.time()

        self.session.on("conversation_item_added", added)
        self.session.on("user_state_changed", user_state)

    async def on_exit(self) -> None:
        if self._router is not None:
            await self._router.close()


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="phone-tree-router")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        userdata=routing.initial_state(),
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
        vad=ctx.proc.userdata["vad"],
    )
    agent = PhoneTreeRouter(ctx.room)
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()
    agent.watch()
    # Both clocks start when the call connects.
    session.userdata["started"] = time.time()
    publish_router(ctx.room, session.userdata)
    await session.generate_reply(
        instructions=f"Say {routing.BRAND} support and ask, in one short sentence, what they need."
    )


if __name__ == "__main__":
    cli.run_app(server)
