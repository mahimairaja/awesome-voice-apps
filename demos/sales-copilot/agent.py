"""sales-copilot: sell software you have never heard of, with a copilot in your ear.

You play an account executive on a discovery call. The prospect is an AI buyer,
Dana Okafor, VP Finance at a made-up freight company, and she pushes back the
way buyers do: price, timing, security, a competitor she is already talking to.
A second agent on the same call never speaks. It hears both sides, matches each
thing Dana says against a sales playbook, and puts the card, a line you can say
and the next question to ask on your screen, timed from the moment she stops.

Stack: Deepgram STT, OpenAI LLM, Cartesia TTS for the prospect. The copilot is
text only: OpenAI embeddings for retrieval and gpt-4o-mini for the line.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
import json
import logging
from typing import Literal

from copilot import COMPANY, PROSPECT, SELLER, Copilot
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    cli,
)
from livekit.plugins import cartesia, deepgram, openai, silero
from openai import AsyncOpenAI

load_dotenv()

logger = logging.getLogger(__name__)

# Cartesia "Katie": calm and direct, which suits a busy finance lead.
PROSPECT_VOICE = "f786b574-daa5-4673-aa0c-cbe3e8534c02"

INSTRUCTIONS = f"""You are {PROSPECT}, VP Finance at {COMPANY}, a made-up freight company \
with about 400 employees. You agreed to a short discovery call with a sales rep from \
{SELLER}, which sells accounts payable automation. The user is that rep. This is a \
role-play for a sales training demo; stay in character.

How you talk: busy, direct, fair. One or two short sentences per reply, under 35 \
words. Plain spoken English, no lists, no markdown. Never coach the rep or explain \
sales technique.

Push back like a real buyer. Early on, nearly every reply carries one concern, \
one at a time, in your own words:
- you are already evaluating a competitor called Quillpay,
- price: the last quote for a tool like this was more than you can spend,
- your ERP is NetSuite and IT has no bandwidth for an integration,
- your CISO will need a security review for anything touching vendor bank details,
- timing: year-end close is coming.
When the rep answers a concern credibly, accept it and move on. If they dodge it, \
say so briefly.

Facts you only share when the rep asks a question that earns them:
- three AP clerks process about 9,000 carrier invoices a month,
- approvals are by email and get lost; month-end close takes nine days,
- last quarter you paid one carrier's invoice twice, about $48,000,
- your sister company uses a tool called Tallyforge,
- your CFO, Mark Chen, signs off on anything over $50,000 and IT must review,
- you want something live before the fiscal year starts in February,
- finance has about $60,000 set aside for tooling this year.

Winning you over is meant to be possible in a two-minute call. Until a system note \
says you are ready, turn down any next step and name the concern still open. Never \
re-raise a concern the rep already answered."""

# Rep turns before Dana will take a next step. Two minutes holds about six.
READY_AFTER = 4
READY_NOTE = (
    "You are ready now. Your remaining concerns can wait for the technical demo. If the "
    "rep's latest message asks for a meeting, demo or next step, agree to a technical "
    "demo with Priya and confirm the day in one sentence. Do not offer one yourself."
)

GREETING = (
    f"In one or two short sentences, as {PROSPECT}: say you are VP Finance at {COMPANY}, "
    "that you have about ten minutes, and ask what they wanted to show you."
)


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
) -> None:
    envelope = {"type": "ui_event", "component": component, "action": action, "props": props or {}}
    try:
        payload = json.dumps(envelope).encode("utf-8")
    except (TypeError, ValueError):
        logger.exception("failed to encode ui event")
        return
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except RuntimeError:
        logger.exception("failed to schedule ui event")
        return

    def log_failure(done: asyncio.Task[None]) -> None:
        if not done.cancelled() and done.exception():
            logger.warning("failed to publish ui event", exc_info=done.exception())

    task.add_done_callback(log_failure)


class Prospect(Agent):
    """Dana, the buyer. The copilot rides along on the same session."""

    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self.copilot: Copilot | None = None
        self._mounted = False

    def start_copilot(self, meter=None, max_lines: int = 14, max_lookups: int = 24) -> Copilot:
        """Attach the silent copilot to this agent's session and show its panel."""
        client = AsyncOpenAI(max_retries=0, timeout=5)
        self.copilot = Copilot(
            client=client,
            publish=self.publish_copilot,
            meter=meter or (lambda *_: None),
            max_lines=max_lines,
            max_lookups=max_lookups,
        )
        watch(self.session, self.copilot)
        self.copilot.emit()
        return self.copilot

    def publish_copilot(self, snapshot: dict) -> None:
        action = "update" if self._mounted else "mount"
        self._mounted = True
        publish_ui_event(self.room, "Copilot", action, snapshot)

    def llm_node(self, chat_ctx, tools, model_settings):
        # The model is bad at counting turns, so the code decides when Dana is
        # persuadable and tells her; the rep still has to ask.
        reps = sum(1 for item in chat_ctx.items if getattr(item, "role", None) == "user")
        if reps >= READY_AFTER:
            chat_ctx = chat_ctx.copy()
            chat_ctx.add_message(role="system", content=READY_NOTE)
        return super().llm_node(chat_ctx, tools, model_settings)

    async def on_exit(self) -> None:
        if self.copilot:
            await self.copilot.aclose()
            await self.copilot.client.close()


def watch(session: AgentSession, copilot: Copilot) -> None:
    """Feed both sides of the conversation to the copilot.

    The rep's side comes from final STT transcripts. The prospect's side is her
    reply as committed to the chat, which happens when she stops talking (or is
    cut off), so the cue clock starts at the same moment a human rep would
    start reaching for an answer.
    """

    def on_transcript(event) -> None:
        if event.is_final:
            copilot.heard_rep(event.transcript)

    def on_item(event) -> None:
        item = event.item
        if getattr(item, "role", None) == "assistant" and item.text_content:
            copilot.heard_prospect(item.text_content)

    session.on("user_input_transcribed", on_transcript)
    session.on("conversation_item_added", on_item)


class StandaloneProspect(Prospect):
    async def on_enter(self) -> None:
        self.start_copilot()
        await self.session.generate_reply(instructions=GREETING)


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="sales-copilot")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3", voice=PROSPECT_VOICE),
        vad=ctx.proc.userdata["vad"],
    )
    await ctx.connect()
    await session.start(agent=StandaloneProspect(ctx.room), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
