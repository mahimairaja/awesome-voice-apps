"""tenant-rights: voice agent for US renter rights, grounded in HUD guidance.

Answers renter questions from a prebaked index of public HUD material, names the
source out loud, and redirects to legal help when a question goes past what the
documents cover. The whole stack runs on NVIDIA under one NVIDIA_API_KEY: Riva
STT, NIM LLM, Riva TTS, and NIM embeddings. The LLM and embeddings reach NIM over
its OpenAI-compatible endpoint, so the openai client is NVIDIA's transport here,
not a second provider.

Run it:
1. Copy .env.example to .env and fill NVIDIA_API_KEY plus the three LiveKit keys.
2. uv sync
3. uv run --no-project python build_index.py (builds the retrieval index once)
4. uv run --no-project python agent.py download-files
5. uv run --no-project python agent.py dev, then open
   https://playground.mahimai.ca/demos/tenant-rights.
"""

import asyncio
import json
import logging
import os
import pathlib
from typing import Literal

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
    StopResponse,
    cli,
)
from livekit.plugins import nvidia, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from rag import NIM_BASE_URL, embed_query, embedding_backend, load_index, retrieve

load_dotenv()

logger = logging.getLogger(__name__)

INDEX_PATH = pathlib.Path(__file__).parent / "data" / "index.npz"
CARD_ID = "source"  # per-answer source card, mounted then updated
NOTICE_ID = "notice"  # standing legal notice, mounted once
TOPICS_ID = "topics"  # standing list of answerable topics, mounted once

# A mid instruct model on NVIDIA NIM. Answers are short and grounded, so you can
# swap to a smaller, faster model (for example meta/llama-3.1-8b-instruct) if
# the conversation feels laggy.
NIM_LLM_MODEL = "meta/llama-3.3-70b-instruct"

# Free the worker if a call runs long or the caller goes quiet.
MAX_CALL_SECONDS = 300
IDLE_HANGUP_SECONDS = 30

INSTRUCTIONS = (
    "You are a warm, knowledgeable voice assistant who helps people understand "
    "their rights as renters in the United States. Talk like a helpful person, "
    "not a search engine. "
    "For greetings and small talk, just answer naturally. "
    "For renter-rights questions, give a real, useful answer. Lean on the source "
    "passages you are given that turn, and you may add well-established general "
    "knowledge about United States renter rights to be genuinely helpful. When a "
    "specific number, deadline, or rule varies by state, give the common or "
    "typical rule and note that it can vary by state, rather than deflecting. "
    "Do not invent exact statute numbers, dollar amounts, or deadlines, or state "
    "a precise figure as certain unless it is in the passages; for high-stakes "
    "specifics, suggest confirming with local legal aid or the state's rules. "
    "The screen already shows the source you are drawing from and a notice that "
    "this is information, not legal advice, so do not name the source or repeat "
    "that disclaimer in every answer; mention the source only when it adds "
    "weight. "
    "You are speaking out loud, so keep replies to one or two sentences. Lead "
    "with the single most useful point and offer to go deeper instead of saying "
    "everything. Use plain words, no lists or symbols, and ask only one question "
    "at a time."
)


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
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
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


def _ui_action(room: rtc.Room, component_id: str) -> Literal["mount", "update"]:
    mounted = getattr(room, "_ui_mounted", None)
    if mounted is None:
        mounted = set()
        setattr(room, "_ui_mounted", mounted)
    if component_id in mounted:
        return "update"
    mounted.add(component_id)
    return "mount"


def _passage(text: str) -> str:
    """Return the full section body (heading stripped, whitespace normalized).

    The card sends the whole passage now; the playground clamps it to a few
    lines and offers a popup so the user can read it in full.
    """
    body = text.split("\n", 1)[-1].strip()
    return " ".join(body.split())


def _publish_card(room: rtc.Room, title: str, body: str, subtitle: str = "") -> None:
    # subtitle is always sent (default empty) because the playground merges update
    # props onto the mounted card; omitting it would leave a stale source line on a
    # later card. The legal notice lives in a standing panel, not on this card.
    publish_ui_event(
        room,
        "Card",
        _ui_action(room, CARD_ID),
        component_id=CARD_ID,
        props={
            "title": title,
            "subtitle": subtitle,
            "body": body,
            "accent": True,
        },
    )


def _publish_static_ui(room: rtc.Room, index: dict) -> None:
    """Mount the two standing panels once: the legal notice and the topic menu.

    Both mount and never update. The topic list is derived from the indexed
    section headings, so it always reflects exactly what the agent can answer.
    """
    publish_ui_event(
        room,
        "Card",
        "mount",
        component_id=NOTICE_ID,
        props={
            "body": (
                "⚠️ For renters in the United States. General information "
                "from HUD guidance and common practice, not legal advice."
            ),
            "accent": True,
        },
    )
    headings: list[str] = []
    seen: set[str] = set()
    for text in index["texts"]:
        heading = text.split("\n", 1)[0].strip()
        if heading and heading not in seen:
            seen.add(heading)
            headings.append(heading)
    publish_ui_event(
        room,
        "List",
        "mount",
        component_id=TOPICS_ID,
        props={
            "title": "You can ask about",
            "items": [{"title": heading} for heading in headings],
        },
    )


def _unmount_card(room: rtc.Room) -> None:
    """Clear the source card so the screen never shows a stale answer.

    Only unmounts when a card is currently up, and forgets the id so the next
    _publish_card mounts fresh rather than updating a card that is gone.
    """
    mounted = getattr(room, "_ui_mounted", None)
    if not mounted or CARD_ID not in mounted:
        return
    mounted.discard(CARD_ID)
    publish_ui_event(room, "Card", "unmount", component_id=CARD_ID)


def build_voice_stack():
    """Build the NVIDIA voice stack: Riva STT, NIM LLM, Riva TTS.

    The LLM talks to NIM over its OpenAI-compatible endpoint, so it is built with
    the openai plugin pointed at NIM_BASE_URL and keyed by NVIDIA_API_KEY. NVIDIA
    has no native LiveKit LLM plugin; this is the documented path.
    """
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "NVIDIA_API_KEY is not set; the tenant-rights stack needs it."
        )
    return (
        nvidia.STT(language_code="en-US"),
        openai.LLM(model=NIM_LLM_MODEL, base_url=NIM_BASE_URL, api_key=api_key),
        nvidia.TTS(voice="Magpie-Multilingual.EN-US.Leo", language_code="en-US"),
    )


class RentersGuide(Agent):
    def __init__(self, room: rtc.Room, index: dict, floor: float) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self._room = room
        self._index = index
        self._floor = floor

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        question = (new_message.text_content or "").strip()
        if not question:
            # Empty or garbled transcript: clear any stale card and suppress the
            # reply rather than let the framework generate one with no grounding.
            _unmount_card(self._room)
            raise StopResponse()

        try:
            query_vec = await embed_query(question)
        except Exception:
            logger.exception("embedding lookup failed")
            turn_ctx.add_message(
                role="assistant",
                content=(
                    "System note: retrieval is temporarily unavailable, so you "
                    "have no source passages for this turn. Do not answer the "
                    "question from general knowledge. Tell the user you cannot "
                    "look that up right now and ask them to try again in a moment."
                ),
            )
            _publish_card(
                self._room,
                title="One moment",
                body="I could not look that up just now. Please try again.",
            )
            return

        result = retrieve(self._index, query_vec, k=3, floor=self._floor)

        if result.covered:
            passages = "\n\n".join(
                f"[Source: {hit.source_label}]\n{hit.text}" for hit in result.hits
            )
            turn_ctx.add_message(
                role="assistant",
                content=(
                    "System note: answer the user's next message helpfully in one "
                    "or two sentences. Use the source passages below as your main "
                    "grounding; you may add well-established general US "
                    "renter-rights knowledge. When a specific number or rule varies "
                    "by state, give the common rule and note it can vary, rather "
                    "than deflecting. Do not state an exact number, deadline, or "
                    "citation as certain unless it is in these passages. The screen "
                    "shows the source, so you need not name it.\n\n" + passages
                ),
            )
            top = result.hits[0]
            # Chunks are stored as "{heading}\n{body}", so the first line is the
            # section. Show the section as the title and the document as the
            # subtitle, so the card names exactly what is being read from.
            heading, _, _ = top.text.partition("\n")
            _publish_card(
                self._room,
                title=heading.strip() or top.source_label,
                subtitle=top.source_label,
                body=_passage(top.text),
            )
        else:
            turn_ctx.add_message(
                role="assistant",
                content=(
                    "System note: no source passage matched this message. If it is "
                    "a greeting or small talk, answer naturally. If it is a United "
                    "States renter-rights question, answer briefly from "
                    "well-established general knowledge: give the common rule and "
                    "note that specifics vary by state. Do not invent exact numbers "
                    "or citations, and for high-stakes specifics suggest checking "
                    "local rules. Keep it to one or two sentences."
                ),
            )
            # No source this turn: clear the source card. The standing notice and
            # topic panels stay up, so the screen never shows a stale citation.
            _unmount_card(self._room)


def _watchdog_done_callback(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.exception("watchdog task failed", exc_info=exc)


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()
    if not INDEX_PATH.exists():
        raise RuntimeError(
            f"index not found at {INDEX_PATH}. "
            "Run: uv run --no-project python build_index.py"
        )
    index = load_index(INDEX_PATH)
    backend = embedding_backend()
    if index["model"] != backend.model:
        built = index["model"] or "an unknown model"
        raise RuntimeError(
            f"index was built with embeddings '{built}' but the current keys "
            f"select '{backend.model}'. Rebuild it: "
            "uv run --no-project python build_index.py"
        )
    proc.userdata["index"] = index
    proc.userdata["floor"] = backend.floor


server.setup_fnc = prewarm


@server.rtc_session(agent_name="tenant-rights")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    stt, llm, tts = build_voice_stack()
    logger.info("tenant-rights using the NVIDIA stack")

    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )

    await session.start(
        agent=RentersGuide(
            ctx.room, ctx.proc.userdata["index"], ctx.proc.userdata["floor"]
        ),
        room=ctx.room,
    )
    await ctx.connect()
    _publish_static_ui(ctx.room, ctx.proc.userdata["index"])

    # --- Call-lifecycle management ---
    # Cap the call and hang up on a quiet caller so the worker is freed. Same
    # pattern proven in roadside-dispatch: a graceful end that speaks one line,
    # deletes the room (disconnecting the caller too), then closes the session;
    # an idle watchdog reset on every user speech event; a hard max-call timer.
    closed = asyncio.Event()
    idle_holder: list[asyncio.Task] = []

    async def _graceful_end(line: str) -> None:
        """Speak the line once, end the room for everyone, then close, once."""
        if closed.is_set():
            return
        closed.set()
        if line:
            try:
                await session.say(line, allow_interruptions=False)
            except Exception:
                logger.exception("lifecycle say() failed")
        try:
            await ctx.delete_room()
        except Exception:
            logger.exception("delete_room failed")
        await session.aclose()

    async def _idle_watchdog() -> None:
        try:
            await asyncio.sleep(IDLE_HANGUP_SECONDS)
        except asyncio.CancelledError:
            return
        if closed.is_set():
            return
        logger.info("idle timeout reached, ending session")
        await _graceful_end("Are you still there? I will close the call now to free the line. Take care.")

    async def _max_call_watchdog() -> None:
        try:
            await asyncio.sleep(MAX_CALL_SECONDS)
        except asyncio.CancelledError:
            return
        if closed.is_set():
            return
        logger.info("max call limit reached, ending session")
        await _graceful_end("We have reached the time limit for this call. Take care.")

    def _on_user_input(_ev) -> None:
        # Any user speech (interim or final) resets the idle timer.
        if closed.is_set():
            return
        if idle_holder and not idle_holder[0].done():
            idle_holder[0].cancel()
        t = asyncio.create_task(_idle_watchdog(), name="idle_watchdog")
        t.add_done_callback(_watchdog_done_callback)
        if idle_holder:
            idle_holder[0] = t
        else:
            idle_holder.append(t)

    session.on("user_input_transcribed", _on_user_input)

    def _cancel_watchdogs(_ev=None) -> None:
        if idle_holder and not idle_holder[0].done():
            idle_holder[0].cancel()
        if not max_task.done():
            max_task.cancel()

    session.on("close", _cancel_watchdogs)

    idle_t = asyncio.create_task(_idle_watchdog(), name="idle_watchdog")
    idle_t.add_done_callback(_watchdog_done_callback)
    idle_holder.append(idle_t)

    max_task = asyncio.create_task(_max_call_watchdog(), name="max_call_watchdog")
    max_task.add_done_callback(_watchdog_done_callback)

    await session.generate_reply(
        instructions=(
            "Greet the user in one short, friendly sentence. Tell them the topics "
            "you can cover are listed on screen and they can ask about any of them. "
            "Do not mention legal advice; an on-screen notice already covers it."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
