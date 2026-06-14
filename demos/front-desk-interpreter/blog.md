---
title: The leanest agent in the cookbook is a hotel interpreter
summary: A speech-to-speech front-desk interpreter on Gemini Live, with no STT, TTS, VAD, or turn detector, and the one model-id gotcha that cost an afternoon.
author: Mahimai
---

## The problem

A hotel front desk gets a guest who speaks no English. Today that means a
phone tree, a paid interpreter line, or a clerk and guest poking at
translation apps. The job is small and well defined: hear one side, say it
back to the other, do nothing else.

## Why this stack

One model does the whole thing. Gemini Live is speech to speech, so there
is no separate STT, no TTS, no VAD model, and no turn detector to wire up
or pay for. The agent is the leanest in the cookbook: an instructions
string, a session, and two event handlers for captions. English anchors
the desk side; the guest language is whatever the model last heard that
was not English, so direction lives in the prompt, not in code.

## The interesting part

The session is the whole runtime. No plugins beyond the realtime model:

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

Captions pair the guest line with its translation: `user_input_transcribed`
accumulates multi-segment finals (one long utterance emits several), and
`conversation_item_added` flushes the pending original onto the assistant
row.

## What surprised me

The model id is load-bearing and the docs lie about the default. Passing
`gemini-2.5-flash` (a text-API id) makes the Live websocket close with a
1008 policy violation: that model has no `bidiGenerateContent`. The id
that works for a Gemini API key is
`gemini-2.5-flash-native-audio-preview-12-2025`. An afternoon went to a
one-line constant.

## Run it

Talk to it at https://playground.mahimai.ca/demos/front-desk-interpreter.
Open in English, switch to any other language mid-call, and hand the phone
back and forth. Or fork the cookbook and run the worker locally.
