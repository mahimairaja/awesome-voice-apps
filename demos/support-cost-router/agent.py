"""support-cost-router: a support line that sends each turn to the cheapest model that can take it.

A fictional carrier's support line. Every caller turn is embedded once, and
that one vector does two jobs: it looks the question up in a semantic answer
cache (a hit is spoken with no model call at all), and it picks a model tier
by comparing the turn to example turns. Small talk goes to gpt-4.1-nano,
account questions to gpt-4.1-mini, disputes and churn risks to gpt-4.1. Each
turn is priced as it ran and as it would have run on gpt-4.1, and the ledger
is published as a UI event so a client can show the difference live.

Stack: Deepgram Nova-3 STT, OpenAI gpt-4.1 family plus text-embedding-3-small,
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
from typing import ClassVar

import numpy as np
import router
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    NOT_GIVEN,
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RunContext,
    cli,
    function_tool,
)
from livekit.plugins import cartesia, deepgram, openai, silero
from openai import AsyncOpenAI

load_dotenv()

logger = logging.getLogger(__name__)

GREETING = (
    f"Thanks for calling {router.BRAND}. This is a demo line, so the account is made up. "
    "What can I help you with?"
)

INSTRUCTIONS = (
    f"You answer the support line at {router.BRAND}, a mobile carrier. The caller is the "
    "account holder. For anything about their bill, charges, plan or usage, call "
    "look_up_bill first and answer from it; never guess an amount. If they report a "
    "charge billed twice, check the bill, and only if the identical line really appears "
    "twice call credit_duplicate with that line's name. Two travel day passes on "
    "different days are two valid charges. If a request needs a tool you do not have, say "
    "a billing specialist will handle it and ask the caller to describe the problem. "
    "For general questions about plans, roaming, "
    "eSIM or stores, answer briefly from common carrier practice and say when something "
    "varies. Never promise anything a tool did not confirm. This is a demo: nothing is "
    "really charged or credited. Keep every reply to one or two short spoken sentences, "
    "plain text, no lists, no markdown."
)

# Thread-safe enough: built once, then only read. Rebuilt on the next call if
# the first build failed.
_index: dict | None = None


async def build_index(client: AsyncOpenAI) -> tuple[dict | None, int]:
    """Embed the cache questions and routing examples once per process."""
    global _index
    if _index is not None:
        return _index, 0
    texts, labels = router.index_texts()
    resp = await client.embeddings.create(model=router.EMBED_MODEL, input=texts)
    vectors = np.asarray([item.embedding for item in resp.data], dtype=np.float32)
    _index = {"vectors": vectors, "labels": labels}
    return _index, resp.usage.total_tokens


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


def context_text(chat_ctx) -> str:
    """The text a model would have read for this turn (instructions included)."""
    parts = []
    for item in chat_ctx.items:
        if item.type == "message":
            parts.append(item.text_content or "")
        elif item.type == "function_call":
            parts.append(item.name + item.arguments)
        elif item.type == "function_call_output":
            parts.append(item.output)
    return "\n".join(parts)


def last_user_message(chat_ctx):
    for item in reversed(chat_ctx.items):
        if item.type == "message" and item.role == "user":
            return item
    return None


class CostRouter(Agent):
    # Extra options for every tier's openai.LLM; the hosted copy adds caps.
    llm_options: ClassVar[dict] = {}

    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=INSTRUCTIONS,
            # The router has to see the final transcript once. A preemptive
            # draft would be routed, and billed, on a partial sentence.
            turn_handling={"preemptive_generation": {"enabled": False}},
        )
        self.room = room
        self.state = router.initial_state()
        self.ledger = router.Ledger()
        self.learned: list[dict] = []
        self._index: dict | None = None
        self._greeted = False
        self._message_id: str | None = None
        self._route: router.Route | None = None
        self._turn: router.Turn | None = None
        self._embeddings = AsyncOpenAI(max_retries=0, timeout=3)
        self._models = {
            tier: openai.LLM(model=model, **self.llm_options)
            for tier, model in router.MODELS.items()
        }
        for model in self._models.values():
            model.on("metrics_collected", self._forward_metrics)

    def _forward_metrics(self, metrics) -> None:
        # The session only listens to its own LLM. These models are called
        # directly, so their usage is re-emitted for anything metering the
        # session (cost dashboards, VoiceGateway).
        self.session.emit("metrics_collected", MetricsCollectedEvent(metrics=metrics))

    def meter_embedding(self, tokens: int) -> None:
        """Bill embedding tokens; the hosted copy reports them to its meter."""

    def publish(self) -> None:
        publish_ui_event(self.room, "CostLedger", router.snapshot(self.ledger, self.learned))

    async def load_index(self) -> None:
        try:
            self._index, tokens = await build_index(self._embeddings)
            self.meter_embedding(tokens)
        except Exception:  # noqa: BLE001 - any failure leaves the call on rules
            # Without an index the router still runs on its rules, with no cache.
            logger.warning("routing index unavailable; rules only")

    async def embed(self, text: str) -> tuple[list[float] | None, int]:
        if self._index is None or not text.strip():
            return None, 0
        try:
            resp = await self._embeddings.embeddings.create(model=router.EMBED_MODEL, input=[text])
        except Exception:  # noqa: BLE001 - a failed lookup must not drop the turn
            logger.warning("embedding failed; routing on rules")
            return None, 0
        return list(resp.data[0].embedding), resp.usage.total_tokens

    async def _route_turn(self, chat_ctx) -> tuple[router.Route, router.Turn, str | None]:
        """Route a new caller turn once; tool follow-ups in the same turn reuse it."""
        message = last_user_message(chat_ctx)
        if message is None:
            if not self._greeted:
                self._greeted = True
                r = router.Route("cache", "Scripted greeting, no model call", answer=GREETING)
                return r, self.ledger.open("(call opens)", r), None
            message_id, text = None, ""
        else:
            message_id, text = message.id, message.text_content or ""
        if message_id is not None and message_id == self._message_id and self._turn:
            return self._route, self._turn, message_id
        started = time.perf_counter()
        vector, tokens = await self.embed(text)
        r = router.route(text, vector, self._index or {}, self.learned, self.state["dispute_turns"])
        router.track_dispute(self.state, r)
        self.meter_embedding(tokens)
        turn = self.ledger.open(text, r, tokens)
        turn.router_ms = round((time.perf_counter() - started) * 1000)
        self._message_id, self._route, self._turn = message_id, r, turn
        return r, turn, message_id

    async def llm_node(self, chat_ctx, tools, model_settings):
        started = time.perf_counter()
        r, turn, _ = await self._route_turn(chat_ctx)
        self.publish()
        if r.tier == "cache":
            turn.first_token_ms = round((time.perf_counter() - started) * 1000)
            router.Ledger.add_skipped(turn, context_text(chat_ctx), r.answer)
            self.publish()
            yield r.answer
            return

        model = self._models[r.tier]
        allowed = router.TIER_TOOLS[r.tier]
        tools = [tool for tool in tools if getattr(tool, "id", None) in allowed]
        usage, words, called_tool, finished = None, [], False, False
        try:
            async with model.chat(
                chat_ctx=chat_ctx,
                tools=tools,
                tool_choice=(model_settings.tool_choice if model_settings and tools else NOT_GIVEN),
                conn_options=self.session.conn_options.llm_conn_options,
            ) as stream:
                async for chunk in stream:
                    if chunk.delta and chunk.delta.content:
                        if turn.first_token_ms is None:
                            turn.first_token_ms = round((time.perf_counter() - started) * 1000)
                        words.append(chunk.delta.content)
                    if chunk.delta and chunk.delta.tool_calls:
                        called_tool = True
                    if chunk.usage:
                        usage = chunk.usage
                    yield chunk
            finished = True
        finally:
            answer = "".join(words)
            if usage:
                router.Ledger.add_usage(
                    turn,
                    r.model,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    usage.prompt_cached_tokens,
                )
            else:
                # Interrupted before the usage chunk: price what was sent and said.
                router.Ledger.add_usage(
                    turn,
                    r.model,
                    router.estimate_tokens(context_text(chat_ctx)),
                    router.estimate_tokens(answer) if answer else 0,
                    0,
                    estimated=True,
                )
            # Only a complete, tool-free answer to a general question is reused,
            # and only within this call: never another caller's account.
            if r.learn and finished and not called_tool and answer.strip() and r.vector:
                self.learned.append(
                    {
                        "key": f"learned-{len(self.learned) + 1}",
                        "vector": r.vector,
                        "answer": answer,
                    }
                )
                r.learn = False
            self.publish()

    @function_tool()
    async def look_up_bill(self, context: RunContext) -> str:
        """Get the caller's latest bill, plan and usage."""
        if self._turn:
            self._turn.tools.append("look_up_bill")
        return router.bill_summary()

    @function_tool()
    async def credit_duplicate(self, context: RunContext, item: str) -> str:
        """Credit a line that appears twice on the bill. Pass the line's name."""
        if self._turn:
            self._turn.tools.append("credit_duplicate")
        return router.credit(self.state, item)

    async def on_exit(self) -> None:
        await self._embeddings.close()
        for model in self._models.values():
            await model.aclose()


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="support-cost-router")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        # Required by the pipeline but never called: CostRouter.llm_node picks
        # a model per turn.
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=cartesia.TTS(model="sonic-3"),
        vad=ctx.proc.userdata["vad"],
    )
    agent = CostRouter(ctx.room)
    await agent.load_index()
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()
    agent.publish()
    await session.generate_reply()


if __name__ == "__main__":
    cli.run_app(server)
