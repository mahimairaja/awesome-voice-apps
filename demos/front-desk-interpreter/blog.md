---
title: How to build a live interpreter voice agent
summary: A speech-to-speech front-desk interpreter on Gemini Live, no STT, TTS, VAD, or turn detector, that bridges two people on a call in real time.
author: Mahimai
github: mahimairaja
---

A hotel front desk gets a guest who speaks no English. The job is small and well
defined: hear one side, say it back to the other, do nothing else. This agent
does exactly that, both directions, in real time.

One model does the whole thing. Gemini Live is speech to speech, so there is no
separate STT, no TTS, no VAD, and no turn detector. The session is the whole
runtime:

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

The desk-side language is not hard-coded. The host picks it in the playground
and it rides the agent-dispatch metadata, arriving as `ctx.job.metadata`. The
agent defaults to English and caps the length, since a browser-minted value is
untrusted.

The catch is that a single session links to one participant, the first to join.
On a two-party call it would hear only one side. So the agent tracks the active
speaker and re-links the session to whoever is talking, which makes it interpret
both ways:

```python
current = getattr(room_io, "linked_participant", None)
if current is None or current.identity != sp.identity:
    room_io.set_participant(sp.identity)
```

Captions pair each line with its translation. `user_input_transcribed`
accumulates the guest's speech (one long utterance emits several final segments),
and `conversation_item_added` flushes that pending original onto the assistant
row when the reply lands:

```python
row: dict = {"text": text}
if pending["original"]:
    row["original"] = pending["original"]
    pending["original"] = None
captions.append(row)
```

One more multiparty detail: the invited guest joins after the initial UI
broadcast, so it asks for the scene and captions once its data handler is
listening, and the agent replays them to just that participant.

> [!WARNING]
> The model id is load-bearing and a text-API id will not work. Passing
> `gemini-2.5-flash` closes the Live socket with a 1008 policy violation: that
> model has no `bidiGenerateContent`. Use
> `gemini-2.5-flash-native-audio-preview-12-2025`.

Build it from an empty folder in the full walkthrough, or talk to the finished
agent at https://playground.mahimai.ca/demos/front-desk-interpreter.
