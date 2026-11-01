"""LiveKitConnector tuned for scripted calls to the hosted demos.

Two things the stock connector gets wrong for these calls:

1. A LiveKit agent publishes two transcripts on the ``lk.transcription`` topic:
   its own speech and the caller's speech as it heard it. Both arrive from the
   agent participant, so DeepEval's connector takes the caller's words for the
   agent's reply and every turn after that is shifted by one. The caller's
   transcript is tagged with the caller's own audio track, so it is kept apart
   here (``heard``) instead of being read as the agent's words. It is what the
   agent's speech to text understood, and ``TranscriptionAccuracyMetric``
   compares it with the script.

2. DeepEval ends an agent turn after a short silence, then starts the caller's
   next line at once. Agents that pause mid-reply (a tool call, a lookup, a
   breath between sentences) were still talking when the caller spoke, so the
   caller talked over them and the reply's first audio landed before the
   caller had finished, which DeepEval records as a 0 ms response. Before each
   caller line, ``PatientSimulator`` (in ``caller.py``) now waits until the
   agent has been quiet for ``quiet_ms``, then hands DeepEval the rest of the
   reply as usual.
"""

from __future__ import annotations

import asyncio
import logging
import time

from deepeval.voice import LiveKitConnector
from deepeval.voice.connectors import audio_utils

TRANSCRIBED_TRACK = "lk.transcribed_track_id"
FINAL = "lk.transcription_final"

logger = logging.getLogger(__name__)


class AgentConnector(LiveKitConnector):
    """LiveKitConnector that waits for the agent and keeps what it heard."""

    def __init__(self, *args, quiet_ms: float = 1500.0, max_wait_s: float = 15.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.quiet_ms = quiet_ms
        self.max_wait_s = max_wait_s
        # Final transcripts of the caller's track, in arrival order.
        self.heard: list[str] = []
        # Seconds spent waiting for the agent to finish before each caller line.
        self.waits: list[float] = []
        # Transcript streams the agent sent about its own speech.
        self.agent_streams = 0

    # 1. What the agent heard -------------------------------------------------

    def _on_transcript_stream(self, reader, participant_identity: str) -> None:
        attributes = getattr(getattr(reader, "info", None), "attributes", None) or {}
        if attributes.get(TRANSCRIBED_TRACK) in self._own_tracks():
            task = self._loop.create_task(self._read_heard(reader))
            self._transcript_tasks.add(task)
            task.add_done_callback(self._transcript_tasks.discard)
            return
        if participant_identity != self.identity:
            self.agent_streams += 1
        super()._on_transcript_stream(reader, participant_identity)

    async def _read_heard(self, reader) -> None:
        try:
            text = await reader.read_all()
        except Exception:
            logger.debug("Reading the agent's transcript of the caller failed.", exc_info=True)
            return
        attributes = getattr(getattr(reader, "info", None), "attributes", None) or {}
        if attributes.get(FINAL) not in (True, "true"):
            return
        text = (text or "").strip()
        if text:
            self.heard.append(text)

    def _own_tracks(self) -> set[str]:
        local = getattr(self._room, "local_participant", None)
        return set(getattr(local, "track_publications", None) or ())

    # 2. Let the agent finish -------------------------------------------------

    async def wait_until_quiet(self) -> float:
        """Wait until no voiced agent audio has arrived for ``quiet_ms``.

        Peeks at the downlink without losing anything: every queued event goes
        back in its original order, so the simulator still reads the agent's
        whole reply. Returns the seconds spent waiting.
        """
        queue = self._out_frames
        if queue is None:
            return 0.0
        started = time.perf_counter()
        last_voiced = None
        while True:
            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
            for event in events:
                queue.put_nowait(event)
                audio = getattr(event, "audio", None)
                if audio and not audio_utils.is_silent(audio, self.silence_threshold_rms):
                    last_voiced = getattr(event, "received_at", None) or time.perf_counter()
            now = time.perf_counter()
            quiet_since = last_voiced if last_voiced is not None else started
            if self.call_ended or (now - quiet_since) * 1000.0 >= self.quiet_ms:
                break
            if now - started >= self.max_wait_s:
                logger.warning(
                    "The agent was still talking after %.0fs; the caller goes on.", self.max_wait_s
                )
                break
            await asyncio.sleep(0.05)
        waited = time.perf_counter() - started
        self.waits.append(waited)
        return waited
