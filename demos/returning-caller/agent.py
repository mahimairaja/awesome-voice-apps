"""returning-caller: a private bank's concierge line that remembers you, with consent.

Call once and mention a trip, a preference or something you are waiting on.
The agent asks before it keeps anything, writes each note with a reason and an
expiry, and summarises the call when you hang up. Call again and it picks up
where you left off. Say "forget that" and the note is deleted on the spot.

Memory lives behind a small store interface: a JSON file per caller here, a
database in production (the hosted playground keeps it server-side).

Stack: Deepgram nova-3 STT, OpenAI gpt-4o-mini, Cartesia sonic-3 TTS.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path

import memory
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
from memory import NoteKind
from openai import AsyncOpenAI

load_dotenv()

logger = logging.getLogger(__name__)

SUMMARY_MODEL = "gpt-4o-mini"
SUMMARY_PROMPT = (
    "Summarise this phone call between a private bank's concierge and a client, for "
    "the concierge to read before the client's next call. Two sentences at most: what "
    "the client wanted and what is still open. No account, card or ID numbers, no "
    "balances, no greetings. If nothing of substance happened, reply with NONE."
)

INSTRUCTIONS = (
    f"You answer the concierge line at {memory.BANK}, a private bank. This is a "
    "simulation: you cannot move money or change accounts, you take requests for the "
    "client's advisor and help with travel, cards, wires, appointments and errands. "
    "Memory rules. You may keep short notes between calls, only with the client's "
    "consent. The first time something is worth keeping, ask in one sentence if you "
    "may keep it on file for their next call, then call record_consent with their "
    "answer. Keep a note with remember: kind preference for how they like things done, "
    "task for something open you or their advisor owe them, context for a time-bound "
    "fact like a trip. Write each note in the third person, one short sentence, and "
    "give the reason it helps next time. Never try to keep account, card or ID numbers, "
    "PINs or passwords; if the client offers one, say you never keep those. If they "
    "say forget that, call forget with the id of the note they mean. If they ask you "
    "to stop remembering them, call forget_everything. Never claim you remember "
    "something that is not in the client file below. Keep replies to one or two short "
    "sentences, plain text, no markdown, no emojis."
)


def greeting(entries: list[dict], now: float) -> str:
    if memory.is_returning(entries, now):
        return (
            f"Greet them as a returning {memory.BANK} client. In one sentence pick up "
            "from the last call or the most pressing open note, and ask if they want to "
            "carry on with it."
        )
    return (
        f"Say this is the {memory.BANK} concierge line, a simulation where nothing real "
        "happens, and ask how you can help today."
    )


class FileStore:
    """One JSON file per caller. Swap for your CRM or database in production."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return []

    def _write(self, entries: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, indent=2))

    async def load(self) -> list[dict]:
        entries = memory.expire(self._read(), time.time())
        self._write(entries)
        return entries

    async def save(self, kind: str, text: str, why: str) -> list[dict]:
        entries = memory.add(self._read(), kind, text, why, time.time())
        self._write(entries)
        return entries

    async def forget(self, memory_id: str, by: str) -> list[dict]:
        entries = memory.forget(self._read(), memory_id, time.time(), by)
        self._write(entries)
        return entries

    async def forget_all(self, by: str) -> list[dict]:
        entries = memory.forget_all(self._read(), time.time(), by)
        self._write(entries)
        return entries


def caller_key(identity: str) -> str:
    """A file name from the caller's identity (a SIP number or participant id)."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", identity)[:64] or "console"


async def summarize(lines: list[str]) -> tuple[str, int, int]:
    """Two sentences for the next call. Returns the text and the token usage."""
    async with AsyncOpenAI(max_retries=0, timeout=6) as client:
        response = await client.chat.completions.create(
            model=SUMMARY_MODEL,
            max_completion_tokens=90,
            store=False,
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": "\n".join(lines)[-6000:]},
            ],
        )
    text = (response.choices[0].message.content or "").strip()
    usage = response.usage
    return text, usage.prompt_tokens if usage else 0, usage.completion_tokens if usage else 0


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


def publish_recall(room: rtc.Room, state: dict) -> None:
    publish_ui_event(room, "Recall", memory.snapshot(state, time.time()))


def initial_state(entries: list[dict] | None = None) -> dict:
    entries = list(entries or [])
    # Notes and the summary carried into this call; consent is not a memory.
    loaded = sum(e["kind"] != "consent" for e in memory.active(entries, time.time()))
    return {"entries": entries, "fresh": set(), "refused": [], "loaded": loaded, "lines": []}


class Concierge(Agent):
    base_instructions = INSTRUCTIONS

    def __init__(self, room: rtc.Room, store: FileStore | None = None) -> None:
        super().__init__(instructions=self.base_instructions)
        self.room = room
        self.store = store
        self._state: dict | None = None
        self._summary_task: asyncio.Task | None = None

    async def load_file(self) -> list[dict]:
        """Read the client file and put it in the instructions for this call."""
        entries = await self.store.load()
        await self.update_instructions(
            self.base_instructions + memory.context_block(entries, time.time())
        )
        return entries

    def _apply(self, state: dict, entries: list[dict]) -> None:
        before = {e["id"] for e in state["entries"]}
        state["fresh"] |= {e["id"] for e in entries if e["id"] not in before}
        state["entries"] = entries
        publish_recall(self.room, state)

    @function_tool()
    async def record_consent(self, context: RunContext[dict], granted: bool) -> str:
        """The client answered whether you may keep notes for their next call."""
        state = context.userdata
        now = time.time()
        on_file = memory.consent(state["entries"], now)
        try:
            if granted:
                if on_file:
                    return "consent already on file"
                self._apply(
                    state,
                    await self.store.save(
                        "consent", "Keep notes between calls.", "Client said yes on the call."
                    ),
                )
                return "consent recorded; you may keep notes now"
            if on_file:
                self._apply(state, await self.store.forget(on_file["id"], "caller"))
                return "consent withdrawn and every note deleted"
        except Exception:
            logger.exception("memory store unavailable")
            return "the client file is unavailable right now; say so"
        return "nothing kept; do not ask again this call"

    @function_tool()
    async def remember(self, context: RunContext[dict], kind: NoteKind, note: str, why: str) -> str:
        """Keep one short note for the client's next call. Needs consent first.

        Args:
            kind: preference, task (something still open) or context (time-bound fact).
            note: one third-person sentence, like "Flying to Lisbon on the 20th."
            why: how it helps next time, like "So the card is not flagged abroad."
        """
        state = context.userdata
        refusal = memory.screen(note)
        if refusal:
            state["refused"] = [
                *state["refused"],
                {"text": memory.clean(memory.scrub(note), 80), "reason": refusal},
            ][-3:]
            publish_recall(self.room, state)
            return f"not kept: {refusal} are never stored. Tell the client."
        try:
            entries = await self.store.save(kind, note, why)
        except ValueError as problem:
            return f"not kept: {problem}"
        except Exception:
            logger.exception("memory store unavailable")
            return "the client file is unavailable right now; say so"
        self._apply(state, entries)
        kept = entries[-1]
        return f"kept as {kept['id']} for {memory.when(kept['expires'] - time.time())}"

    @function_tool()
    async def forget(self, context: RunContext[dict], memory_id: str) -> str:
        """Delete one note the client asked you to forget, by its id."""
        try:
            entries = await self.store.forget(memory_id, "caller")
        except ValueError:
            return "no such note; ask which one they mean"
        except Exception:
            logger.exception("memory store unavailable")
            return "the client file is unavailable right now; say so"
        self._apply(context.userdata, entries)
        return "deleted; confirm it is gone in a few words"

    @function_tool()
    async def forget_everything(self, context: RunContext[dict]) -> str:
        """The client wants nothing kept: withdraw consent and delete every note."""
        try:
            entries = await self.store.forget_all("caller")
        except Exception:
            logger.exception("memory store unavailable")
            return "the client file is unavailable right now; say so"
        self._apply(context.userdata, entries)
        return "everything deleted and consent withdrawn"

    def watch_transcript(self) -> None:
        # Kept in memory for the summary only; the transcript itself is never stored.
        self._state = state = self.session.userdata

        def added(event) -> None:
            item = event.item
            role = getattr(item, "role", None)
            text = getattr(item, "text_content", None)
            if role in ("user", "assistant") and text:
                lines = state["lines"]
                lines.append(f"{'Client' if role == 'user' else 'Concierge'}: {text[:400]}")
                del lines[:-30]

        self.session.on("conversation_item_added", added)

    def write_summary(self) -> asyncio.Task:
        """Summarise the call once, after it ends, if the client consented."""
        if self._summary_task is None:
            self._summary_task = asyncio.ensure_future(self._summarize())
        return self._summary_task

    async def _summarize(self) -> None:
        state = self._state
        if (
            not state
            or not memory.consent(state["entries"], time.time())
            or len(state["lines"]) < 3
        ):
            return
        try:
            text, prompt_tokens, completion_tokens = await summarize(state["lines"])
            self.meter_summary(prompt_tokens, completion_tokens)
            if text and text.upper() != "NONE":
                state["entries"] = await self.store.save(
                    "summary", text, "Written after the call so the next one picks up here."
                )
        except Exception:
            logger.exception("call summary not written")

    def meter_summary(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Hook for billing the post-call summary; the hosted copy meters it."""


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="returning-caller")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    await ctx.connect()
    caller = await ctx.wait_for_participant()
    store = FileStore(Path(".memory") / f"{caller_key(caller.identity)}.json")

    agent = Concierge(ctx.room, store)
    entries = await agent.load_file()
    session = AgentSession(
        userdata=initial_state(entries),
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
        vad=ctx.proc.userdata["vad"],
    )

    async def wrap_up(_reason: str) -> None:
        await agent.write_summary()

    ctx.add_shutdown_callback(wrap_up)
    await session.start(agent=agent, room=ctx.room)
    agent.watch_transcript()
    publish_recall(ctx.room, session.userdata)
    await session.generate_reply(instructions=greeting(entries, time.time()))


if __name__ == "__main__":
    cli.run_app(server)
