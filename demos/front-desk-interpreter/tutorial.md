---
title: How to build a live interpreter voice agent
summary: Build a speech-to-speech front-desk interpreter on Gemini Live that bridges two people on one call, both directions, with live captions, from an empty folder to a running worker.
author: Mahimai
github: mahimairaja
---

## What you will build

A live interpreter for a hotel front desk. A guest speaks any language, the desk
hears their own language, and replies come back in the guest's language, with
live captions on screen. Two people join the same call on their own devices; the
agent interprets between them and does nothing else. The finished demo is
`front-desk-interpreter`.

You need one Google AI key and three LiveKit values. And `uv`. The whole stack
is Gemini Live, so the one key covers hearing, interpreting, and speaking.

## 1. Scaffold

Pin Python to `3.11`. `pyproject.toml` pulls only the Google plugin:

```toml
[project]
name = "front-desk-interpreter"
version = "0.1.0"
description = "Live interpreter at a hotel front desk: guests speak any language, the desk hears English."
requires-python = ">=3.11"
dependencies = [
    "livekit-agents[google]>=1.5,<2.0",
    "python-dotenv>=1.0",
]
```

`.env.example` is four lines:

```
# Full Gemini Live stack: one key covers hearing, interpreting, and speaking.
GOOGLE_API_KEY=
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
```

## 2. The realtime session

Gemini Live is speech to speech, so there is no STT, TTS, VAD, or turn detector.
The session is the whole runtime, with input and output transcription turned on
so we can draw captions:

```python
session = AgentSession(
    llm=google.realtime.RealtimeModel(
        model=GEMINI_LIVE_MODEL,
        voice="Puck",
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    ),
)
```

> [!WARNING]
> The model id is load-bearing. A text-API id like `gemini-2.5-flash` closes the
> Live socket with a 1008 policy violation, because it has no
> `bidiGenerateContent`. Use `gemini-2.5-flash-native-audio-preview-12-2025`.

## 3. The interpreter prompt and the desk language

The whole behavior is one instructions string. The desk-side language is picked
by the host in the playground and rides the agent-dispatch metadata, arriving as
`ctx.job.metadata`. That value is minted in a browser, so it is untrusted:
default to English, and cap the length before it lands in the prompt.

```python
def resolve_target_language(metadata: str | None) -> str:
    target = (metadata or "").strip()
    if not target:
        return DEFAULT_TARGET_LANGUAGE
    return target[:MAX_TARGET_LANGUAGE_LEN]
```

The prompt tells the model to interpret faithfully in the first person, keep
questions as questions, and never answer or add opinions.

## 4. Two people, one session: switch the active speaker

A single `AgentSession` links to one participant, the first to join. On a
two-party call it would hear only one side. So the agent tracks the active
speaker and re-links the session to whoever is talking:

```python
def on_active_speakers(speakers: list[rtc.Participant]) -> None:
    if agent_speaking["now"]:
        return
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
            room_io.set_participant(sp.identity)
        break

ctx.room.on("active_speakers_changed", on_active_speakers)
```

> [!IMPORTANT]
> Skip the switch while the agent is speaking. Its synthesized audio can echo
> through a participant's mic and would otherwise yank the link mid-sentence. An
> `agent_state_changed` handler sets `agent_speaking["now"]`, and the switch
> returns early when it is true.

## 5. Live captions

Two events build the caption rows. `user_input_transcribed` accumulates the
guest's speech, since one long utterance emits several final segments, and
`conversation_item_added` flushes that pending original onto the assistant row
when the reply lands:

```python
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
```

## 6. The late joiner

The invited guest joins after the agent has already broadcast its UI, and the
playground drops updates for a component it never mounted. Replaying on
`participant_connected` would race the guest's data handler, which only attaches
after it finishes connecting its media. So the guest asks for the UI once it is
listening, and the agent replays the current scene and captions to just that
participant:

```python
def on_ui_request(packet: rtc.DataPacket) -> None:
    if packet.topic != UI_REQUEST_TOPIC or packet.participant is None:
        return
    identity = packet.participant.identity
    _publish_scene(ctx.room, to=identity)
    if captions:
        _publish_captions(ctx.room, captions, to=identity)

ctx.room.on("data_received", on_ui_request)
```

The publish helper passes `destination_identities=[identity]` so the replay
targets one late joiner without re-mounting on the clients already in the call.

## 7. Why there is no automated eval

Every other cookbook demo carries a text-mode behavioral eval. This one does
not: the eval harness feeds text turns, and a speech-to-speech realtime model
has no text pipeline to drive, so there is nothing to assert. It is the one demo
verified by hand instead.

## 8. Run it

```sh
cp .env.example .env
```

```sh
uv sync
```

```sh
uv run python agent.py dev
```

Open https://playground.mahimai.ca/demos/front-desk-interpreter, invite a guest
on their own device, and hand the conversation back and forth in two languages.
