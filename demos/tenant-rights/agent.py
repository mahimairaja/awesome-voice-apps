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

INSTRUCTIONS = (
    "You are a warm, plain-spoken voice assistant who helps people understand "
    "their rights as renters in the United States, drawing on public HUD "
    "guidance. Talk like a helpful person, not a search engine. "
    "For greetings, small talk, or questions about what you can help with, just "
    "answer naturally and point the user to a topic they can ask about. "
    "For renter-rights questions, answer only from the source passages you are "
    'given that turn, and open by naming the source, for example "According to '
    'HUD\'s resident rights guidance." When you are given no passage for a '
    "renter-rights question, say in one sentence that you do not have that "
    "specific detail, and suggest a related topic you can cover. "
    "Never invent statute numbers, dollar amounts, deadlines, or citations; if a "
    "detail is not in your sources, say you do not have it. You explain what the "
    "documents say; you do not give legal advice, interpret a specific lease, or "
    "predict how a situation will turn out. A standing on-screen notice already "
    "tells the user this is information and not legal advice, so do not repeat "
    "that in your answers. "
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


def _snippet(text: str, limit: int = 200) -> str:
    body = text.split("\n", 1)[-1].strip()
    body = " ".join(body.split())
    if len(body) <= limit:
        return body
    return body[: limit - 3].rstrip() + "..."


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
                "⚠️ This shares what public HUD guidance says. It is "
                "general information, not legal advice."
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
                    "System note: answer the user's next message using only the "
                    "source passages below. Open by naming the source, for example "
                    '"According to HUD\'s resident rights guidance," then give the '
                    "single most useful point in one or two sentences and offer to "
                    "go deeper. Do not recite every passage. If a specific number, "
                    "deadline, dollar amount, or citation is not in these passages, "
                    "say you do not have it. If the passages do not actually answer "
                    "the question, say in one sentence that you do not have that "
                    "detail.\n\n" + passages
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
                body=_snippet(top.text),
            )
        else:
            turn_ctx.add_message(
                role="assistant",
                content=(
                    "System note: no source passage matched this message. If it is a "
                    "greeting, small talk, or a question about what you can help "
                    "with, answer it naturally in one or two sentences and mention a "
                    "topic the user can ask about. If it is a specific renter-rights "
                    "question, say in one sentence that you do not have that exact "
                    "detail and suggest a related topic. Do not answer renter-rights "
                    "questions from general knowledge, and do not recite legal "
                    "disclaimers."
                ),
            )
            # No source this turn: clear the source card. The standing notice and
            # topic panels stay up, so the screen never shows a stale citation.
            _unmount_card(self._room)


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
    await session.generate_reply(
        instructions=(
            "Greet the user in one short, friendly sentence. Tell them the topics "
            "you can cover are listed on screen and they can ask about any of them. "
            "Do not mention legal advice; an on-screen notice already covers it."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
