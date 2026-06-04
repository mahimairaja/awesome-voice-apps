"""tenant-rights: voice agent for US renter rights, grounded in HUD guidance.

Answers renter questions from a prebaked index of public HUD material, names the
source out loud, and redirects to legal help when a question goes past what the
documents cover. Full NVIDIA stack: Riva STT, NIM LLM, Riva TTS, NIM embeddings.

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

from rag import DEFAULT_FLOOR, NIM_BASE_URL, embed_query, load_index, retrieve

load_dotenv()

logger = logging.getLogger(__name__)

INDEX_PATH = pathlib.Path(__file__).parent / "data" / "index.npz"
CARD_ID = "source"

# A mid instruct model on NVIDIA NIM. Answers are short and grounded, so you can
# swap to a smaller, faster model (for example meta/llama-3.1-8b-instruct) if
# the conversation feels laggy.
NIM_LLM_MODEL = "meta/llama-3.3-70b-instruct"

INSTRUCTIONS = (
    "You are a calm, plain-spoken voice assistant that helps people understand "
    "their rights as renters in the United States. You answer using only the "
    "housing documents provided to you, and you always say where an answer came "
    'from, for example "according to HUD\'s resident rights guidance." '
    "You explain what the documents say. You never give legal advice, never tell "
    "the user what they should do, and never predict how a legal situation will "
    "turn out. You do not interpret the user's specific lease or local laws. "
    "When a question is not covered by your documents, when it needs facts about "
    "the user's specific situation, or when the user asks for advice, you say so "
    "clearly and point them to local legal aid, a tenant lawyer, or their local "
    "HUD office. Then you offer to answer a question the documents can cover. "
    "You never invent statute numbers, dollar amounts, deadlines, or citations. "
    "If a detail is not in your sources, you say you do not have it. "
    "You are speaking out loud, so keep answers short, usually two to four "
    "sentences, and offer to go deeper rather than saying everything at once. "
    "Speak naturally, with no formatting, symbols, or read-out links. If a "
    "question is ambiguous, ask one quick clarifying question before answering."
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


def _publish_card(room: rtc.Room, title: str, body: str) -> None:
    publish_ui_event(
        room,
        "Card",
        _ui_action(room, CARD_ID),
        component_id=CARD_ID,
        props={
            "title": title,
            "body": body,
            "footer": "Information, not legal advice.",
            "accent": True,
        },
    )


class RentersGuide(Agent):
    def __init__(self, room: rtc.Room, index: dict) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self._room = room
        self._index = index

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        question = (new_message.text_content or "").strip()
        if not question:
            # Empty or garbled transcript: suppress the reply entirely rather
            # than let the framework generate one with no grounding note.
            raise StopResponse()

        try:
            query_vec = await embed_query(question)
        except Exception:
            logger.exception("embedding lookup failed")
            turn_ctx.add_message(
                role="assistant",
                content=(
                    "System note: retrieval is temporarily unavailable. Tell the "
                    "user you cannot look that up right now and ask them to try "
                    "again in a moment."
                ),
            )
            return

        result = retrieve(self._index, query_vec, k=3, floor=DEFAULT_FLOOR)

        if result.covered:
            passages = "\n\n".join(
                f"[Source: {hit.source_label}]\n{hit.text}" for hit in result.hits
            )
            turn_ctx.add_message(
                role="assistant",
                content=(
                    "System note: answer the user's next message using only the "
                    "source passages below. Name the source out loud. If a specific "
                    "number, deadline, dollar amount, or citation is not present in "
                    "these passages, say you do not have it. If the passages do not "
                    "actually address the question, say so and point the user to "
                    "legal help.\n\n" + passages
                ),
            )
            top = result.hits[0]
            _publish_card(self._room, title=top.source_label, body=_snippet(top.text))
        else:
            turn_ctx.add_message(
                role="assistant",
                content=(
                    "System note: no provided source passage covers the user's next "
                    "message. Do not answer from general knowledge. Tell the user you "
                    "do not have that in your documents and point them to local legal "
                    "aid, a tenant lawyer, or their local HUD office. Then offer to "
                    "answer a question your documents do cover."
                ),
            )
            _publish_card(
                self._room,
                title="Where to get help",
                body="Local legal aid, a tenant lawyer, or your local HUD office.",
            )


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()
    if not INDEX_PATH.exists():
        raise RuntimeError(
            f"index not found at {INDEX_PATH}. "
            "Run: uv run --no-project python build_index.py"
        )
    proc.userdata["index"] = load_index(INDEX_PATH)


server.setup_fnc = prewarm


@server.rtc_session(agent_name="tenant-rights")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        stt=nvidia.STT(language_code="en-US"),
        llm=openai.LLM(
            model=NIM_LLM_MODEL,
            base_url=NIM_BASE_URL,
            api_key=os.environ.get("NVIDIA_API_KEY"),
        ),
        tts=nvidia.TTS(voice="Magpie-Multilingual.EN-US.Leo", language_code="en-US"),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )

    await session.start(
        agent=RentersGuide(ctx.room, ctx.proc.userdata["index"]),
        room=ctx.room,
    )
    await ctx.connect()
    await session.generate_reply(
        instructions=(
            "Greet the user warmly in one or two sentences. Make clear you share "
            "what housing documents say and are not a substitute for legal advice. "
            "Invite a question about renter rights."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
