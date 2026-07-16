---
title: How to build a panel-scribe voice agent with live diarization
summary: Build a recruiting-debrief scribe that separates each interviewer's voice with pyannoteAI Live, stays silent until asked, and writes an attributed scorecard, from an empty folder to a running worker, on Deepgram, Cerebras, and Rime.
---

## What you will build

A scribe for a hiring-panel debrief. Three interviewers talk about a candidate
around one microphone; the agent labels each voice live, keeps a who-said-what
transcript with talk-time, and on "scribe, recap" writes an attributed scorecard
and reads it back. Diarization is load-bearing here because several voices share
one audio track. The finished demo is `panel-scribe`.

You need four provider keys (Deepgram, Cerebras, Rime, pyannoteAI) and three
LiveKit values. And `uv`.

## 1. Scaffold

Pin Python to `3.11`. `pyproject.toml` adds `aiohttp` (the pyannote REST and
WebSocket) and `numpy` (int16 to float32) to the voice plugins; the eval judge
stays in a dev group so it never ships in the stack:

```toml
dependencies = [
    "livekit-agents[deepgram,cerebras,rime]>=1.6,<2.0",
    "aiohttp>=3.9",
    "numpy>=1.26",
    "python-dotenv>=1.0",
]

[dependency-groups]
dev = ["livekit-agents[openai]>=1.6,<2.0"]
```

`.env.example` lists seven keys: `DEEPGRAM_API_KEY`, `CEREBRAS_API_KEY`,
`RIME_API_KEY`, `PYANNOTE_API_KEY`, and the three `LIVEKIT_*` values.

## 2. The pyannote Live sidecar (the hero)

pyannoteAI Live is a cloud WebSocket, no local model. POST for a pre-authorized
socket, then connect:

```python
async def connect(self) -> None:
    self._http = aiohttp.ClientSession()
    resp = await self._http.post(
        PYANNOTE_REST_URL,
        headers={"Authorization": f"Bearer {self._api_key}"},
    )
    resp.raise_for_status()
    body = await resp.json()
    url = body.get("stream", {}).get("url") or body.get("url") or body.get("wsUrl")
    if not url:
        raise RuntimeError(f"pyannote live: no stream url in {body}")
    self._ws = await self._http.ws_connect(url)
```

Its messages are `diarization_speaker_start` / `diarization_speaker_end` events.
A pure `on_event` tracks who is speaking now and each speaker's cumulative
seconds, so it is unit-testable without a network:

```python
def on_event(self, msg: dict, now: float) -> None:
    kind = msg.get("type")
    speaker = self._speaker_of(msg)
    if kind == "diarization_speaker_start" and speaker:
        self.label(speaker)
        self.active.add(speaker)
        self._started_at[speaker] = now
        self.current_speaker = speaker
    elif kind == "diarization_speaker_end" and speaker:
        self.active.discard(speaker)
        start = self._started_at.pop(speaker, None)
        if start is not None:
            self.seconds[speaker] = self.seconds.get(speaker, 0.0) + max(0.0, now - start)
        if self.current_speaker == speaker:
            self.current_speaker = next(iter(self.active), self.current_speaker)
```

A pump task resamples the queued frames to 16 kHz mono, converts int16 to
Float32, and sends the raw bytes:

```python
for out in resampler.push(frame):
    f32 = np.frombuffer(out.data, dtype=np.int16).astype(np.float32) / 32768.0
    if self._ws and not self._ws.closed:
        await self._ws.send_bytes(f32.tobytes())
```

> [!NOTE]
> The real event schema is `{"type": "diarization_speaker_start", "data":
> {"speaker": "SPEAKER_00"}}`, but the pre-authorized url arrives at the
> top-level `url`, not `stream.url`, so `connect` checks both.

## 3. The passive scribe

The audio the sidecar reads is the same audio Deepgram reads. Rather than open a
second stream, the agent forks each STT frame inside `stt_node`, teeing it to the
sidecar before yielding it to the default STT:

```python
async def stt_node(self, audio, model_settings):
    async def tee():
        async for frame in audio:
            self.sidecar.feed_frame(frame)
            yield frame

    async for event in Agent.default.stt_node(self, tee(), model_settings):
        yield event
```

The panel talks among themselves, so the scribe must stay silent. Every finished
turn records its line, prefixes it with the active speaker (only when pyannote
has labeled one), publishes the UI, then raises `StopResponse`, unless the turn
contains the trigger word:

```python
async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
    text = (new_message.text_content or "").strip()
    if not text:
        raise StopResponse()
    speaker = self.sidecar.display_speaker()
    if self.sidecar.current_speaker is not None:
        new_message.content = [f"[{speaker}] {text}"]
    self.transcript.append({"speaker": speaker, "text": text})
    publish_transcript(self.room, self.transcript)
    loop = asyncio.get_running_loop()
    publish_talk_time(self.room, self.sidecar.talk_time_items(now=loop.time()))
    if TRIGGER not in text.lower():
        raise StopResponse()
```

> [!IMPORTANT]
> Prefix the speaker only when `current_speaker` is set. During a diarization
> gap it is None, and injecting "Speaker ?" would muddy attribution in the
> scorecard.

## 4. The scorecard and the UI

Because each line enters the model context already tagged with its speaker, the
recap needs no separate state: on "scribe, recap" the LLM reads its own
attributed history and calls one tool, which renders a Table and a Card:

```python
@function_tool()
async def publish_scorecard(self, context: RunContext, rows: list[dict], consensus: str) -> str:
    """Render the debrief scorecard on screen.

    rows: one dict per interviewer with keys interviewer, strengths,
    concerns, lean. consensus: one sentence overall hiring lean.
    """
    publish_scorecard_ui(self.room, rows, consensus)
    return "scorecard published"
```

The live transcript renders as Captions, talk-time as Meters (the biggest talker
flagged as the driver), and the scorecard as a Table plus a consensus Card.

## 5. The stack

The session runs the trio; the sidecar starts after connect, with a done-callback
so a pyannote outage is logged rather than silent:

```python
session = AgentSession(
    stt=deepgram.STT(model="nova-3", language="en"),
    llm=cerebras.LLM(model="gpt-oss-120b"),
    tts=rime.TTS(model="arcana", speaker="celeste", use_websocket=True),
    vad=ctx.proc.userdata["vad"],
    turn_detection=inference.TurnDetector(),
)
```

## 6. The eval

The diarization path needs audio, so a text eval covers the trio instead. It
feeds pre-labeled turns (what reconciliation would produce), fires the trigger,
and asserts the tool call plus the judges:

```python
await session.run(user_input="[Speaker 1] The candidate was strong on system design.")
await session.run(user_input="[Speaker 2] I am worried the testing story was thin.")
await session.run(user_input="[Speaker 3] Communication was fine, nothing stood out.")

result = await session.run(user_input="scribe, give us the recap")
result.expect.contains_function_call(name="publish_scorecard")
```

## 7. Run it

```sh
cp .env.example .env
```

```sh
uv sync
```

```sh
uv run python agent.py dev
```

Open https://playground.mahimai.ca/demos/panel-scribe. Diarization needs more
than one voice, so play a panel recording into the mic or gather a few people,
then say "scribe, recap" and watch the attributed scorecard land.
