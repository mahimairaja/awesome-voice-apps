"""panel-scribe: recruiting-panel scribe with live speaker diarization.

Three interviewers debrief a candidate around one microphone. pyannoteAI Live
labels each voice in real time, Deepgram transcribes the words, and on "scribe,
recap" Cerebras writes an attributed scorecard that Rime reads back.

Stack: Deepgram Nova-3 STT, Cerebras LLM, Rime TTS, pyannoteAI Live diarization.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py dev, then open
   https://playground.mahimai.ca/demos/panel-scribe.
"""

import asyncio
import json
import logging

import aiohttp
import numpy as np
from dotenv import load_dotenv
from livekit import rtc

load_dotenv()

logger = logging.getLogger(__name__)

PYANNOTE_REST_URL = "https://api.pyannote.ai/v1/live"
PYANNOTE_SAMPLE_RATE = 16000
TRIGGER = "scribe"


class PyannoteLive:
    """Streams PCM to pyannoteAI Live and tracks who is speaking now.

    Pure event logic is in on_event/label/talk_time_items so it is unit-testable
    without a network. connect/pump/run own the sockets.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self.active: set[str] = set()
        self.current_speaker: str | None = None
        self.seconds: dict[str, float] = {}
        self._started_at: dict[str, float] = {}
        self._labels: dict[str, str] = {}
        self._http: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._in_rate: int | None = None
        self._closed = False

    # --- pure logic (tested) -------------------------------------------------
    def label(self, raw: str) -> str:
        if raw not in self._labels:
            self._labels[raw] = f"Speaker {len(self._labels) + 1}"
        return self._labels[raw]

    @staticmethod
    def _speaker_of(msg: dict) -> str | None:
        data = msg.get("data")
        if isinstance(data, dict) and data.get("speaker"):
            return data["speaker"]
        return msg.get("speaker")

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
        elif kind == "error":
            logger.warning("pyannote live error: %s", msg.get("message") or msg)

    def display_speaker(self) -> str:
        return self.label(self.current_speaker) if self.current_speaker else "Speaker ?"

    def talk_time_items(self, now: float) -> list[dict]:
        live = dict(self.seconds)
        for spk, start in self._started_at.items():
            live[spk] = live.get(spk, 0.0) + max(0.0, now - start)
        total = sum(live.values())
        if total <= 0:
            return []
        top = max(live, key=live.get)
        return [
            {
                "label": self.label(spk),
                "value": round(secs / total, 3),
                "driver": spk == top,
            }
            for spk, secs in sorted(live.items(), key=lambda kv: self.label(kv[0]))
        ]

    # --- networking ----------------------------------------------------------
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
        logger.info("pyannote live connected")

    def feed_frame(self, frame: rtc.AudioFrame) -> None:
        """Called from stt_node with a raw agent audio frame (non-blocking)."""
        if self._in_rate is None:
            self._in_rate = frame.sample_rate
        try:
            self._queue.put_nowait(bytes(frame.data))
        except asyncio.QueueFull:
            logger.debug("pyannote queue full, dropping frame")

    async def _pump(self) -> None:
        resampler: rtc.AudioResampler | None = None
        while not self._closed:
            raw = await self._queue.get()
            in_rate = self._in_rate or PYANNOTE_SAMPLE_RATE
            if resampler is None:
                # frames are int16 mono from the mic; resample to 16 kHz.
                resampler = rtc.AudioResampler(
                    input_rate=in_rate, output_rate=PYANNOTE_SAMPLE_RATE, num_channels=1
                )
            samples = np.frombuffer(raw, dtype=np.int16)
            frame = rtc.AudioFrame(
                data=samples.tobytes(),
                sample_rate=in_rate,
                num_channels=1,
                samples_per_channel=len(samples),
            )
            for out in resampler.push(frame):
                f32 = np.frombuffer(out.data, dtype=np.int16).astype(np.float32) / 32768.0
                if self._ws and not self._ws.closed:
                    await self._ws.send_bytes(f32.tobytes())

    async def run(self) -> None:
        """Connect, then read events until closed. Pump runs concurrently."""
        await self.connect()
        pump = asyncio.create_task(self._pump())
        try:
            assert self._ws is not None
            loop = asyncio.get_running_loop()
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        self.on_event(json.loads(msg.data), now=loop.time())
                    except json.JSONDecodeError:
                        logger.debug("pyannote non-json message: %s", msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        finally:
            self._closed = True
            pump.cancel()

    async def aclose(self) -> None:
        self._closed = True
        if self._ws:
            await self._ws.close()
        if self._http:
            await self._http.close()
