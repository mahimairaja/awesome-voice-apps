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
import os
from typing import Literal

import aiohttp
import numpy as np
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    StopResponse,
    cli,
    function_tool,
    inference,
)
from livekit.plugins import cerebras, deepgram, rime

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


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
    component_id: str | None = None,
) -> None:
    envelope = {"type": "ui_event", "component": component, "action": action, "props": props or {}}
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

    def log_publish_failure(task: asyncio.Task[None]) -> None:
        try:
            task.result()
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


def publish_transcript(room: rtc.Room, lines: list[dict]) -> None:
    items = [
        {"text": f"{ln['speaker']}: {ln['text']}", "original": ln["text"]} for ln in lines[-20:]
    ]
    publish_ui_event(
        room,
        "Captions",
        _ui_action(room, "transcript"),
        component_id="transcript",
        props={"title": "panel transcript", "items": items},
    )


def publish_talk_time(room: rtc.Room, items: list[dict]) -> None:
    publish_ui_event(
        room,
        "Meters",
        _ui_action(room, "talk-time"),
        component_id="talk-time",
        props={"title": "talk time", "items": items},
    )


def publish_scorecard_ui(room: rtc.Room, rows: list[dict], consensus: str) -> None:
    table_rows = [
        [r.get("interviewer", ""), r.get("strengths", ""), r.get("concerns", ""), r.get("lean", "")]
        for r in rows
    ]
    publish_ui_event(
        room,
        "Table",
        _ui_action(room, "scorecard"),
        component_id="scorecard",
        props={
            "title": "debrief scorecard",
            "columns": ["interviewer", "strengths", "concerns", "lean"],
            "rows": table_rows,
        },
    )
    publish_ui_event(
        room,
        "Card",
        _ui_action(room, "consensus"),
        component_id="consensus",
        props={"title": "panel consensus", "body": consensus, "accent": True},
    )


INSTRUCTIONS = (
    "You are a scribe for a hiring panel debrief. Several interviewers are "
    "talking to each other about a candidate. Stay silent and just listen. Do "
    "not answer or comment on what they say. Only speak when someone says the "
    "word scribe, for example scribe recap or scribe give us the scorecard. "
    "When that happens, review the whole conversation so far, where each line is "
    "prefixed with the speaker like Speaker 1 or Speaker 2, and call "
    "publish_scorecard once. Give one row per interviewer with their strengths, "
    "their concerns, and their lean toward hiring, plus a one sentence panel "
    "consensus. After the tool call, say a short spoken summary in one or two "
    "sentences. Keep speech plain text, no markdown, no emojis."
)


class PanelScribe(Agent):
    def __init__(self, room: rtc.Room, sidecar: PyannoteLive) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self.sidecar = sidecar
        self.transcript: list[dict] = []

    async def stt_node(self, audio, model_settings):
        async def tee():
            async for frame in audio:
                self.sidecar.feed_frame(frame)
                yield frame

        async for event in Agent.default.stt_node(self, tee(), model_settings):
            yield event

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        text = (new_message.text_content or "").strip()
        if not text:
            raise StopResponse()
        speaker = self.sidecar.display_speaker()
        new_message.content = [f"[{speaker}] {text}"]
        self.transcript.append({"speaker": speaker, "text": text})
        publish_transcript(self.room, self.transcript)
        loop = asyncio.get_running_loop()
        publish_talk_time(self.room, self.sidecar.talk_time_items(now=loop.time()))
        if TRIGGER not in text.lower():
            raise StopResponse()

    @function_tool()
    async def publish_scorecard(self, context: RunContext, rows: list[dict], consensus: str) -> str:
        """Render the debrief scorecard on screen.

        rows: one dict per interviewer with keys interviewer, strengths,
        concerns, lean. consensus: one sentence overall hiring lean.
        """
        publish_scorecard_ui(self.room, rows, consensus)
        return "scorecard published"


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = inference.VAD()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="panel-scribe")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}

    pyannote_key = os.environ.get("PYANNOTE_API_KEY", "")
    sidecar = PyannoteLive(api_key=pyannote_key)

    session = AgentSession(
        stt=deepgram.STT(model="nova-3", language="en"),
        llm=cerebras.LLM(model="llama-3.3-70b"),
        tts=rime.TTS(model="arcana", speaker="celeste", use_websocket=True),
        vad=ctx.proc.userdata["vad"],
        turn_detection=inference.TurnDetector(),
    )

    agent = PanelScribe(ctx.room, sidecar)
    await session.start(agent=agent, room=ctx.room)
    await ctx.connect()

    if pyannote_key:
        asyncio.create_task(sidecar.run())

        async def _stop_sidecar() -> None:
            await sidecar.aclose()

        ctx.add_shutdown_callback(_stop_sidecar)
    else:
        logger.warning("PYANNOTE_API_KEY not set: diarization disabled, labels will be Speaker ?")

    publish_transcript(ctx.room, [])
    publish_talk_time(ctx.room, [])
    await session.generate_reply(
        instructions=(
            "Greet the panel in one sentence: say you are listening and labeling "
            "each voice, and that they should say scribe recap when they want the "
            "scorecard. Then stop."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
