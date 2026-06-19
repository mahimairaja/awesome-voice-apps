"""front-desk-interpreter: live interpreter at a hotel front desk.

A guest speaks any language, the desk hears English, and replies come back in
the guest's language, with live captions on screen. Speech to speech on the
Gemini Live API: no STT, no TTS, no VAD model, no turn detector.

Run it:
1. Copy .env.example to .env and fill GOOGLE_API_KEY plus the three LiveKit keys.
2. uv sync
3. uv run python agent.py dev, then open
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

# A late joiner asks for the current UI on this topic once its data handler is
# attached; the agent replays the scene and captions to just that participant.
UI_REQUEST_TOPIC = "ui_request"


GEMINI_LIVE_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

# The desk-side anchor language. The host picks it on the playground; it rides
# the agent-dispatch metadata and arrives as ctx.job.metadata. Everything the
# guest says is rendered into this language, and this language is rendered back
# into whatever the guest speaks.
DEFAULT_TARGET_LANGUAGE = "English"
MAX_TARGET_LANGUAGE_LEN = 40


def resolve_target_language(metadata: str | None) -> str:
    """Read the target language from dispatch metadata, with a safe fallback.

    The token is minted in the visitor's browser, so the value is untrusted:
    fall back to English when empty and cap the length before it lands in the
    instructions string.
    """
    target = (metadata or "").strip()
    if not target:
        return DEFAULT_TARGET_LANGUAGE
    return target[:MAX_TARGET_LANGUAGE_LEN]


def build_instructions(target: str) -> str:
    return (
        f"You are a live interpreter on a call between two people. One is a "
        f"desk clerk who speaks {target}; the other is a guest who may speak "
        f"any language. They are on their own devices on the same call. You "
        f"interpret between them and do nothing else. When you hear a language "
        f"other than {target}, say it again in {target}. When you hear "
        f"{target}, say it again in the language the guest has been speaking. "
        f"If {target} is spoken before the guest has said anything, say in one "
        f"short {target} sentence that you are ready and the guest may speak "
        f"any language. Interpret faithfully and completely, in the first "
        f"person, keeping questions as questions. Never answer questions "
        f"yourself, never add advice or opinions, never summarize. Keep exactly "
        f"the meaning and tone of what was said. Greet once at the start, in "
        f"{target}, with one sentence that explains the setup."
    )


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
    component_id: str | None = None,
    destination_identities: list[str] | None = None,
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
            room.local_participant.publish_data(
                payload,
                topic="ui",
                reliable=True,
                # Empty list broadcasts to everyone; a specific identity targets
                # a single late joiner without re-mounting on existing clients.
                destination_identities=destination_identities or [],
            )
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


def _publish_scene(room: rtc.Room, to: str | None = None) -> None:
    # A targeted publish is a replay to a late joiner, so force a fresh mount;
    # the broadcast path keeps its mount-once-then-update bookkeeping.
    action = "mount" if to else _ui_action(room, "scene")
    publish_ui_event(
        room,
        "Card",
        action,
        component_id="scene",
        props={
            "title": "Front desk interpreter",
            "body": (
                "Invite a guest to join on their own device. Speak any language; "
                "the interpreter relays both ways."
            ),
            "accent": True,
        },
        destination_identities=[to] if to else None,
    )


def _publish_captions(room: rtc.Room, rows: list[dict], to: str | None = None) -> None:
    action = "mount" if to else _ui_action(room, "captions")
    publish_ui_event(
        room,
        "Captions",
        action,
        component_id="captions",
        props={"title": "live captions", "items": rows},
        destination_identities=[to] if to else None,
    )


class FrontDeskInterpreter(Agent):
    def __init__(self, target_language: str = DEFAULT_TARGET_LANGUAGE) -> None:
        super().__init__(instructions=build_instructions(target_language))


server = AgentServer()


# No prewarm: the Gemini Live API owns hearing, turn-taking, and speaking, so
# there is no VAD or turn-detector model to preload.


@server.rtc_session(agent_name="front-desk-interpreter")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    target_language = resolve_target_language(ctx.job.metadata)
    logger.info("interpreting with desk language %s", target_language)

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
        # session.room_io raises until the session is started; this event only
        # fires after connect, but read the backing attribute so an early fire
        # is skipped rather than raised.
        room_io = getattr(session, "_room_io", None)
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

    def on_ui_request(packet: rtc.DataPacket) -> None:
        # A late joiner (the invited guest) missed the initial broadcast mounts,
        # and the playground drops UI updates for a component it never mounted.
        # Replaying on participant_connected would race the joiner's data
        # handler, which only attaches after it finishes connecting its media.
        # Instead the joiner asks for the UI once it is listening, and we replay
        # the current scene and captions to just that participant.
        if packet.topic != UI_REQUEST_TOPIC or packet.participant is None:
            return
        identity = packet.participant.identity
        _publish_scene(ctx.room, to=identity)
        if captions:
            _publish_captions(ctx.room, captions, to=identity)

    ctx.room.on("data_received", on_ui_request)

    await session.start(agent=FrontDeskInterpreter(target_language), room=ctx.room)
    await ctx.connect()
    _publish_scene(ctx.room)
    await session.generate_reply(
        instructions=(
            f"Greet in one short {target_language} sentence: you are the front "
            f"desk interpreter, the guest may speak any language, and the desk "
            f"hears {target_language}."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
