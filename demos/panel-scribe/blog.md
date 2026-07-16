---
title: How to build a panel-scribe voice agent with live diarization
summary: A recruiting-debrief scribe that separates each interviewer's voice live with pyannoteAI Live, keeps a who-said-what transcript, and writes an attributed scorecard, on Deepgram, Cerebras, and Rime.
---

A hiring debrief is three or four people talking over one speakerphone. The
useful record is not the words, it is who said them: which interviewer loved the
system design, which one flagged the thin testing story. One microphone, several
voices, and plain transcription flattens them into one wall of text.

Diarization is the hero, so it drives the stack. pyannoteAI Live is a cloud
WebSocket: stream 16 kHz PCM, get speaker-turn labels back in real time, no local
model and no GPU. Deepgram Nova-3 transcribes the words, Cerebras GPT-OSS 120B
writes the scorecard, and Rime reads it back. The trio is the voice; pyannote is
the ears.

The sidecar POSTs for a pre-authorized socket, then streams the room's audio to
it:

```python
resp = await self._http.post(
    PYANNOTE_REST_URL, headers={"Authorization": f"Bearer {self._api_key}"}
)
resp.raise_for_status()
body = await resp.json()
url = body.get("stream", {}).get("url") or body.get("url") or body.get("wsUrl")
self._ws = await self._http.ws_connect(url)
```

That audio comes from forking the STT frames, so one source feeds both Deepgram
and pyannote with no second subscription:

```python
async def stt_node(self, audio, model_settings):
    async def tee():
        async for frame in audio:
            self.sidecar.feed_frame(frame)
            yield frame

    async for event in Agent.default.stt_node(self, tee(), model_settings):
        yield event
```

The agent has to stay quiet. A panel talks among themselves, so every finished
turn records its line, prefixed with the active speaker, then raises
`StopResponse`, unless the turn contains the word scribe:

```python
speaker = self.sidecar.display_speaker()
if self.sidecar.current_speaker is not None:
    new_message.content = [f"[{speaker}] {text}"]
self.transcript.append({"speaker": speaker, "text": text})
...
if TRIGGER not in text.lower():
    raise StopResponse()
```

Because each line enters the model's context already tagged with its speaker, the
recap needs no separate state: on "scribe, recap" the LLM reads its own
attributed history and calls `publish_scorecard`.

> [!NOTE]
> Diarization needs more than one voice. A solo tester hears nothing
> interesting, so play a panel recording into the mic or gather a few people. The
> eval feeds pre-labeled turns to test the recap without audio.

Build it from an empty folder in the full walkthrough, or talk to the finished
agent at https://playground.mahimai.ca/demos/panel-scribe.
