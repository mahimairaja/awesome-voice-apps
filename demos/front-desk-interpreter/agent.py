"""front-desk-interpreter: live interpreter at a hotel front desk.

A guest speaks any language, the desk hears English, and replies come back in
the guest's language, with live captions on screen. Speech to speech on the
Gemini Live API: no STT, no TTS, no VAD model, no turn detector.

Run it:
1. Copy .env.example to .env and fill GOOGLE_API_KEY plus the three LiveKit keys.
2. uv sync
3. uv run --no-project python agent.py dev, then open
   https://playground.mahimai.ca/demos/front-desk-interpreter.
"""

import asyncio
import json
import logging
from typing import Literal

from dotenv import load_dotenv
from google.genai import types
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    JobContext,
    UserInputTranscribedEvent,
    cli,
)
from livekit.agents.llm import ChatMessage
from livekit.plugins import google

load_dotenv()

logger = logging.getLogger(__name__)

# The agent shows the last N exchanges; the playground panel caps at 20.
MAX_CAPTION_ROWS = 8


GEMINI_LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

INSTRUCTIONS = (
    "You are a live interpreter on a call between two people. One is a desk "
    "clerk who speaks English; the other is a guest who may speak any "
    "language. They are on their own devices on the same call. You interpret "
    "between them and do nothing else. When you hear a language other than "
    "English, say it again in English. When you hear English, say it again in "
    "the language the guest has been speaking. If English is spoken before the "
    "guest has said anything, say in one short English sentence that you are "
    "ready and the guest may speak any language. Interpret faithfully and "
    "completely, in the first person, keeping questions as questions. Never "
    "answer questions yourself, never add advice or opinions, never summarize. "
    "Keep exactly the meaning and tone of what was said. Greet once at the "
    "start, in English, with one sentence that explains the setup."
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
    mounted = getattr(room, "_awesome_voice_ui_mounted", None)
    if mounted is None:
        mounted = set()
        setattr(room, "_awesome_voice_ui_mounted", mounted)
    if component_id in mounted:
        return "update"
    mounted.add(component_id)
    return "mount"


def _publish_scene(room: rtc.Room) -> None:
    publish_ui_event(
        room,
        "Card",
        _ui_action(room, "scene"),
        component_id="scene",
        props={
            "title": "Front desk interpreter",
            "body": (
                "Hand the phone across the counter. Speak any language; the desk hears English."
            ),
            "accent": True,
        },
    )


def _publish_captions(room: rtc.Room, rows: list[dict]) -> None:
    publish_ui_event(
        room,
        "Captions",
        _ui_action(room, "captions"),
        component_id="captions",
        props={"title": "live captions", "items": rows},
    )


class FrontDeskInterpreter(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=INSTRUCTIONS)


server = AgentServer()


# No prewarm: the Gemini Live API owns hearing, turn-taking, and speaking, so
# there is no VAD or turn-detector model to preload.


@server.rtc_session(agent_name="front-desk-interpreter")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        llm=google.realtime.RealtimeModel(
            model=GEMINI_LIVE_MODEL,
            voice="Puck",
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        ),
    )

    captions: list[dict] = []
    pending: dict = {"original": None}

    @session.on("user_input_transcribed")
    def on_user_transcribed(ev: UserInputTranscribedEvent) -> None:
        # Finals mark transcription segments, not whole turns, so a long
        # utterance can emit several. Accumulate until the reply consumes them.
        text = (ev.transcript or "").strip()
        if ev.is_final and text:
            prior = pending["original"]
            pending["original"] = f"{prior} {text}" if prior else text

    @session.on("conversation_item_added")
    def on_item_added(ev: ConversationItemAddedEvent) -> None:
        if not isinstance(ev.item, ChatMessage) or ev.item.role != "assistant":
            return
        text = (ev.item.text_content or "").strip()
        if not text:
            return
        row: dict = {"text": text}
        if pending["original"]:
            row["original"] = pending["original"]
            pending["original"] = None
        captions.append(row)
        del captions[:-MAX_CAPTION_ROWS]
        _publish_captions(ctx.room, captions)

    # A single AgentSession links to one participant (the first to join), so on
    # a two-party call the agent would hear only one side and ignore the other.
    # Track the active speaker and re-link the session to whoever is talking, so
    # it interprets both directions. Skip the switch while the agent itself is
    # speaking: its synthesized audio can echo through a participant's mic and
    # would otherwise yank the link mid-sentence.
    agent_speaking = {"now": False}

    @session.on("agent_state_changed")
    def on_agent_state(ev: AgentStateChangedEvent) -> None:
        agent_speaking["now"] = ev.new_state == "speaking"

    def on_active_speakers(speakers: list[rtc.Participant]) -> None:
        if agent_speaking["now"]:
            return
        room_io = session.room_io
        if room_io is None:
            return
        local_identity = ctx.room.local_participant.identity
        for sp in speakers:
            if sp.identity == local_identity:
                continue
            if sp.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
                continue
            current = getattr(room_io, "linked_participant", None)
            if current is None or current.identity != sp.identity:
                try:
                    room_io.set_participant(sp.identity)
                    logger.info("interpreting active speaker %s", sp.identity)
                except Exception:
                    logger.exception("failed to switch linked participant")
            break

    ctx.room.on("active_speakers_changed", on_active_speakers)

    await session.start(agent=FrontDeskInterpreter(), room=ctx.room)
    await ctx.connect()
    _publish_scene(ctx.room)
    await session.generate_reply(
        instructions=(
            "Greet in one short English sentence: you are the front desk "
            "interpreter, the guest may speak any language, and the desk "
            "hears English."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
