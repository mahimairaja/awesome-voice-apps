"""loan-callback: a long loan application that survives a dropped call.

The caller applies for a small business loan with a fictional lender. Every answer
is saved to a durable checkpoint before the agent moves on. If the line drops, the
next call loads that checkpoint and picks up at the exact question, carrying a few
lines of state instead of the old transcript. Within a call, older turns are folded
into the same short state message, so the prompt stays under a token budget.

Stack: Deepgram Nova-3 STT, OpenAI gpt-4o-mini, Cartesia Sonic 3 TTS.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
import copy
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Protocol

import application
from application import FieldName, estimate_tokens
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
    function_tool,
    llm,
)
from livekit.agents.metrics import LLMMetrics
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger(__name__)

# Conversation items kept verbatim; everything older lives in the state message.
KEEP_ITEMS = 6
# Prompt budget per LLM request. Compaction drops more history before it is exceeded.
TOKEN_BUDGET = 1500

INSTRUCTIONS = (
    f"You take small business loan applications by phone for {application.LENDER}. "
    "A system message called the application state lists what is already answered, "
    "notes from earlier in the conversation, and the next question; it is the source "
    "of truth, even across calls. Ask one question at a time, in order, in a few words. "
    "When the caller answers, call record_answer with the field and the value. Pass "
    "years and money as plain numbers, like 4 or 250000. Put anything else worth "
    "remembering for the underwriter (a deadline, a reason, a constraint) in note as one "
    "short sentence, otherwise leave note empty. If they answer several questions at "
    "once, record each. If a tool rejects a value, say why in one sentence and ask "
    "again. Never ask again for something already answered unless the caller wants to "
    "change it. When every question is answered, read back the amount and the use of "
    "funds and ask whether to submit; on yes call submit_application and give the "
    "reference. Plain text only, no markdown, no emojis."
)


class CheckpointStore(Protocol):
    """Where the application lives between calls."""

    name: str

    async def load(self) -> dict | None: ...

    async def save(self, state: dict, view: dict) -> None: ...


class FileStore:
    """One JSON file per caller. Swap for Redis, Postgres or D1 in production."""

    name = "local file"

    def __init__(self, folder: Path, caller: str) -> None:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", caller)[:64] or "caller"
        self.path = folder / f"{safe}.json"

    async def load(self) -> dict | None:
        try:
            text = await asyncio.to_thread(self.path.read_text)
            return json.loads(text)["state"]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    async def save(self, state: dict, view: dict) -> None:
        def write() -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.path.with_suffix(".tmp")
            partial.write_text(json.dumps({"state": state, "view": view}))
            # Atomic rename: a crash mid-write never leaves half a checkpoint.
            partial.replace(self.path)

        await asyncio.to_thread(write)


def item_text(item: llm.ChatItem) -> str:
    if item.type == "message":
        return item.text_content or ""
    if item.type == "function_call":
        return f"{item.name}({item.arguments})"
    if item.type == "function_call_output":
        return item.output
    return ""


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


class LoanCallback(Agent):
    def __init__(self, room: rtc.Room, store: CheckpointStore | None = None) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self.store = store
        self.state = application.new_application()
        self.resumed = False
        # What the new call starts with instead of the old transcript.
        self.carried: dict | None = None
        self.saved: dict | None = None
        # Prompt tokens per LLM request: sent, and what full history would have cost.
        self.budget: list[dict] = []
        self._pending: list[int] = []
        self._prior_tokens = 0
        self._save_lock = asyncio.Lock()

    async def restore(self) -> dict:
        """Load the caller's checkpoint before the first word is spoken."""
        saved = None
        if self.store:
            try:
                saved = await self.store.load()
            except Exception:  # noqa: BLE001 - a store outage starts a fresh application
                logger.warning("checkpoint load failed; starting a new application")
        self.state, self.resumed = application.resume(saved)
        self._prior_tokens = self.state["transcript_tokens"]
        if self.resumed:
            text = application.context_message(self.state)
            self.carried = {
                "text": text,
                "tokens": estimate_tokens(text),
                "prior": self._prior_tokens,
            }
        return self.state

    def opening(self) -> str:
        """The instruction for the first thing the agent says on this call."""
        nxt = application.current(self.state)
        question = (
            application.PROMPTS[nxt]
            if nxt
            else "whether they want to submit the application, after reading back the amount"
        )
        if self.resumed:
            first = self.state["answers"].get("owner_name", "").split(" ")[0]
            welcome = f"welcome {first} back" if first else "welcome them back"
            return (
                "The caller's line dropped during their last call and they called back. "
                f"In one short sentence {welcome}, say every answer was kept, then ask "
                f"only: {question}"
            )
        return (
            f"Say this is a small business loan application simulation for "
            f"{application.LENDER}: use made-up details, nothing is real. Say that if "
            f"the line drops they can call back and pick up where they left off. Then "
            f"ask only: {question}"
        )

    def watch(self) -> None:
        """Count transcript size and prompt tokens for the budget panel."""

        def added(event) -> None:
            item = event.item
            if getattr(item, "role", None) in ("user", "assistant"):
                self.state["transcript_tokens"] += estimate_tokens(item.text_content or "")

        self.session.on("conversation_item_added", added)
        model = self.llm if isinstance(self.llm, llm.LLM) else self.session.llm
        if isinstance(model, llm.LLM):
            model.on("metrics_collected", self.on_llm_metrics)

    def on_llm_metrics(self, metrics) -> None:
        if not isinstance(metrics, LLMMetrics) or not self._pending:
            return
        dropped = self._pending.pop(0)
        sent = metrics.prompt_tokens
        self.budget = [*self.budget, {"sent": sent, "full": sent + dropped}][-12:]
        self.publish()

    def compact(self, chat_ctx: llm.ChatContext) -> tuple[llm.ChatContext, int]:
        """Instructions, the state message, and the last few turns. Nothing else.

        Returns the context to send and an estimate of the tokens left out,
        including the transcripts of earlier calls.
        """
        items = list(chat_ctx.items)

        def system(item) -> bool:
            return item.type == "message" and item.role in ("system", "developer")

        # Turn instructions (generate_reply) arrive as system messages at the end.
        trailing = []
        while items and system(items[-1]):
            trailing.insert(0, items.pop())
        head = [item for item in items if system(item)][:1]
        convo = [
            item
            for item in items
            if not system(item)
            and item.type in ("message", "function_call", "function_call_output")
        ]
        state = llm.ChatMessage(
            role="system",
            content=["Application state:\n" + application.context_message(self.state)],
        )
        keep = KEEP_ITEMS
        while True:
            tail = convo[-keep:] if keep else []
            # Never start on a tool call or result whose partner was dropped.
            while tail and tail[0].type in ("function_call", "function_call_output"):
                tail = tail[1:]
            compact = llm.ChatContext([*head, state, *tail, *trailing])
            size = sum(estimate_tokens(item_text(item)) for item in compact.items)
            if keep <= 2 or size <= TOKEN_BUDGET:
                break
            keep -= 2
        kept = {item.id for item in tail}
        dropped = sum(estimate_tokens(item_text(i)) for i in convo if i.id not in kept)
        return compact, dropped + self._prior_tokens

    def llm_node(self, chat_ctx, tools, model_settings):
        compact, dropped = self.compact(chat_ctx)
        # A request that never reports metrics must not shift every later pairing.
        self._pending = [*self._pending[-3:], dropped]
        return Agent.default.llm_node(self, compact, tools, model_settings)

    async def checkpoint(self) -> bool:
        """Write the whole application to the store before the agent moves on."""
        if not self.store:
            return True
        async with self._save_lock:
            self.state["version"] += 1
            started = time.perf_counter()
            ok = True
            try:
                await self.store.save(copy.deepcopy(self.state), application.snapshot(self.state))
            except Exception:  # noqa: BLE001 - the call goes on; the panel shows the miss
                logger.warning("checkpoint save failed")
                ok = False
            self.saved = {
                "version": self.state["version"],
                "ms": round((time.perf_counter() - started) * 1000),
                "ok": ok,
            }
        self.publish()
        return ok

    def view(self) -> dict:
        return {
            **application.snapshot(self.state),
            "resumed": self.resumed,
            "carried": self.carried,
            "saved": self.saved,
            "store": self.store.name if self.store else "memory only",
            "budget": {"limit": TOKEN_BUDGET, "turns": self.budget},
        }

    def publish(self) -> None:
        publish_ui_event(self.room, "Resume", self.view())

    @function_tool()
    async def record_answer(self, field: FieldName, value: str, note: str = "") -> str:
        """Record the caller's answer to one application question.

        Args:
            field: The question being answered.
            value: The answer. Years and money as plain numbers, like 4 or 250000.
            note: One short sentence worth keeping for later, or empty.
        """
        result = application.record(self.state, field, value, note)
        if result.startswith("rejected"):
            self.publish()
            return result
        if not await self.checkpoint():
            result += " The checkpoint did not save; carry on."
        return result

    @function_tool()
    async def submit_application(self) -> str:
        """Submit the application once every question is answered and the caller agrees."""
        result = application.submit(self.state)
        if result.startswith("submitted"):
            await self.checkpoint()
        else:
            self.publish()
        return result


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="loan-callback")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    await ctx.connect()
    # Key the checkpoint on who is calling: a phone number or account id in production.
    caller = os.getenv("CALLER_ID") or next(
        (p.identity for p in ctx.room.remote_participants.values()), "console"
    )
    store = FileStore(Path(os.getenv("CHECKPOINT_DIR", ".checkpoints")), caller)
    agent = LoanCallback(ctx.room, store)
    await agent.restore()

    session = AgentSession(
        userdata=agent.state,
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
        vad=ctx.proc.userdata["vad"],
    )
    await session.start(agent=agent, room=ctx.room)
    agent.watch()
    agent.publish()
    await session.generate_reply(instructions=agent.opening())


if __name__ == "__main__":
    cli.run_app(server)
