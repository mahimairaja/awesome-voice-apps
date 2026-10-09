"""fair-cancellation: a membership cancel line where policy is code, not prompt.

A subscriber to Lumora+, a made-up streaming service, calls to cancel. The
agent may make one fair retention offer, matched to the reason they give. A
policy engine (policy.py) sits around the model: it hears the cancel request
itself, speaks the "say cancel at any time" disclosure, drops pressure lines
and unapproved deals before they reach the voice, and cancels on its own once
the caller declines or three turns pass, then reads the confirmation number.

Stack: Deepgram Nova-3 STT, OpenAI gpt-4o-mini, Cartesia Sonic 3 TTS.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterable
from typing import Literal

import policy
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    ModelSettings,
    RunContext,
    StopResponse,
    cli,
    function_tool,
    llm,
)
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    f"You answer the membership line for {policy.SPOKEN_BRAND}, a streaming service. "
    "The caller's account is on their screen. Help with whatever they ask. If they want "
    "to cancel, ask once, in one short question, why they are leaving, and call "
    "note_reason with price, not_watching, switching or other. Then follow the tool: "
    "if it says an offer applies, call make_offer and present only what it approved, "
    "in one sentence. If they clearly say yes to it, call accept_offer. If they say no, "
    "or anything else, call cancel_membership. Never invent discounts, prices, pauses "
    "or credits, never make a second offer, never ask if they are sure, and never "
    "warn them about what they will lose. A policy engine checks every sentence you "
    "say and speaks confirmations itself, so do not read reference numbers. If a tool "
    "says blocked, follow what it says. Keep replies to one or two short sentences, "
    "plain text, no markdown, no emojis."
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


def publish_cancel(room: rtc.Room, state: dict) -> None:
    publish_ui_event(room, "Cancel", policy.snapshot(state))


class CancelLine(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        # The policy hears every finished caller turn before the model does.
        state = self.session.userdata
        text = new_message.text_content or ""
        state["last_caller"] = text[:500]
        must_cancel = policy.observe_caller(state, text)
        if must_cancel:
            policy.cancel(state, by="policy")
        publish_cancel(self.room, state)
        if must_cancel:
            # The model gets no turn: code speaks the cancellation and its number.
            self.session.say(policy.take_script(state), allow_interruptions=False)
            publish_cancel(self.room, state)
            raise StopResponse()

    @function_tool()
    async def note_reason(
        self,
        context: RunContext[dict],
        reason: Literal["price", "not_watching", "switching", "other"],
    ) -> str:
        """Record why the caller wants to cancel."""
        result = policy.note_reason(context.userdata, reason)
        publish_cancel(self.room, context.userdata)
        return result

    @function_tool()
    async def make_offer(self, context: RunContext[dict]) -> str:
        """Ask the policy for the one retention offer that fits the reason, if any."""
        result = policy.make_offer(context.userdata)
        publish_cancel(self.room, context.userdata)
        return result

    @function_tool()
    async def accept_offer(self, context: RunContext[dict]) -> str:
        """The caller clearly said yes to the offer. The policy checks their words."""
        state = context.userdata
        result = policy.accept_offer(state, state.get("last_caller", ""))
        publish_cancel(self.room, state)
        return result

    @function_tool()
    async def cancel_membership(self, context: RunContext[dict]) -> str:
        """Cancel the caller's membership. Only after they asked to cancel."""
        result = policy.cancel(context.userdata)
        publish_cancel(self.room, context.userdata)
        return result

    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        state = self.session.userdata

        async def spoken() -> AsyncIterable[str]:
            # Required words first, from code. Then the model, one screened sentence
            # at a time, so a dropped line never reaches the voice.
            script = policy.take_script(state)
            if script:
                publish_cancel(self.room, state)
                yield script + " "
            buffer = ""
            async for chunk in text:
                ready, buffer = policy.split_sentences(buffer + chunk)
                for sentence in ready:
                    kept = policy.screen(state, sentence)
                    if kept:
                        yield kept
                    else:
                        publish_cancel(self.room, state)
            if buffer:
                kept = policy.screen(state, buffer)
                if kept:
                    yield kept
                else:
                    publish_cancel(self.room, state)

        async for frame in super().tts_node(spoken(), model_settings):
            yield frame


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="fair-cancellation")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        userdata=policy.initial_state(),
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
        vad=ctx.proc.userdata["vad"],
    )
    await session.start(agent=CancelLine(ctx.room), room=ctx.room)
    await ctx.connect()
    publish_cancel(ctx.room, session.userdata)
    await session.generate_reply(
        instructions=f"Greet the caller as the {policy.SPOKEN_BRAND} membership line and "
        "ask how you can help."
    )


if __name__ == "__main__":
    cli.run_app(server)
