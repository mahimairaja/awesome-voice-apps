---
title: A hiring-panel scribe that knows who said what
summary: A recruiting debrief agent that separates every interviewer's voice live with pyannoteAI Live streaming diarization, on Deepgram, Cerebras, and Rime.
---

## The problem

A hiring debrief is three or four people talking over one speakerphone. The
useful record is not the words alone, it is who said them: which interviewer
loved the system design, which one flagged the thin testing story. One
microphone, several voices, and plain transcription flattens them into one wall
of text.

## Why this stack

Diarization is the hero, so it drives the choice. pyannoteAI Live is a cloud
WebSocket: stream 16 kHz PCM, get speaker-turn labels back in real time, no
local model and no GPU. Deepgram Nova-3 transcribes the words, Cerebras writes
the scorecard fast enough to feel instant, and Rime reads the summary back. The
trio is the voice; pyannote is the ears.

## The interesting part

The agent has to stay quiet. A panel talks among themselves, and a scribe that
answered every pause would be unusable. So it forks the audio and stays passive:
each STT frame is teed to pyannote inside `stt_node`, and every finished turn
records the line, then raises `StopResponse`, unless the turn contains the word
scribe.

```python
async def on_user_turn_completed(self, turn_ctx, new_message):
    speaker = self.sidecar.display_speaker()
    text = new_message.text_content or ""
    new_message.content = [f"[{speaker}] {text}"]
    self.transcript.append({"speaker": speaker, "text": text})
    if TRIGGER not in text.lower():
        raise StopResponse()
```

Because each line enters the model's context already tagged with its speaker,
the recap needs no separate state: the LLM reads its own attributed history and
writes the scorecard.

## The one gotcha

Diarization needs more than one voice. A solo tester hears nothing interesting,
because there is only one speaker to separate. The demo tells you to play a
panel recording into the mic or gather a few people, and the eval feeds
pre-labeled turns so it can test the recap without audio.

## Run it

Talk to it at https://playground.mahimai.ca/demos/panel-scribe, or fork the
cookbook and run the worker locally.
