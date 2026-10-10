"""Maintainer-owned adapter for the interview coach demo and its hosted call limits."""

import importlib.util
from pathlib import Path

from livekit.agents import Agent
from livekit.plugins import cartesia, deepgram, openai

# Keep the contributed demo self-contained; load it by path so its `agent`
# module never shadows the coffee demo's `agent` module.
_demo = Path(__file__).resolve().parents[1] / "demos" / "interview-coach"
_spec = importlib.util.spec_from_file_location("playground_interview_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
InterviewDesk = _module.InterviewDesk
CascadeInterviewer = _module.CascadeInterviewer
DuplexInterviewer = _module.DuplexInterviewer
ROUND_SECONDS = _module.ROUND_SECONDS

# Per interviewer: one opening, two replies, and room for a barge-in or two.
CASCADE_LLM_REQUESTS = 6
CASCADE_TTS_BYTES = 1200
DUPLEX_RESPONSES = 8


def order_for(reservation: str) -> tuple[str, str]:
    """Which architecture interviews first. The site derives the same order from
    the reservation id to score the visitor's vote, so never change this rule."""
    return ("duplex", "cascade") if int(reservation[-1], 16) % 2 else ("cascade", "duplex")


def round_seconds(call_seconds: int) -> float:
    # Two rounds share the call; a short allowance shortens both.
    return max(20.0, min(ROUND_SECONDS, call_seconds / 2 - 8))


class Lobby(Agent):
    """Never speaks: it sets up hosting, then hands the call to the first interviewer."""

    def __init__(self, room) -> None:
        super().__init__(instructions="Say nothing.")
        self.room = room


def bounded_builder(stop, spawn):
    """Interviewers with per-call caps; `stop` ends the call when one is exceeded."""

    class BoundedCascade(CascadeInterviewer):
        def __init__(self, label, question):
            # Fresh clients per call keep component metrics isolated between calls.
            super().__init__(
                label,
                question,
                stt=deepgram.STT(model="nova-3"),
                llm=openai.LLM(
                    model="gpt-4o-mini", max_completion_tokens=120, max_retries=0, store=False
                ),
                tts=cartesia.TTS(model="sonic-3"),
            )
            self._llm_requests = 0
            self._tts_bytes = 0

        async def llm_node(self, chat_ctx, tools, model_settings):
            self._llm_requests += 1
            if self._llm_requests > CASCADE_LLM_REQUESTS:
                spawn(stop())
                return
            async for chunk in super().llm_node(chat_ctx, tools, model_settings):
                yield chunk

        async def tts_node(self, text, model_settings):
            async def bounded_text():
                async for chunk in text:
                    self._tts_bytes += len(chunk.encode())
                    if self._tts_bytes > CASCADE_TTS_BYTES:
                        spawn(stop())
                        return
                    yield chunk

            async for frame in super().tts_node(bounded_text(), model_settings):
                yield frame

    class BoundedDuplex(DuplexInterviewer):
        def __init__(self, label, question):
            super().__init__(
                label,
                question,
                model=openai.realtime.GPTLiveModel(
                    voice="marin",
                    responses_options={
                        "model": "gpt-5.6-luna",
                        "max_output_tokens": 200,
                        "reasoning": {"effort": "low"},
                    },
                ),
            )
            self._responses = 0

        def _count(self, event):
            if (
                event.get("type") == "response.event"
                and event.get("event", {}).get("type") == "response.created"
            ):
                self._responses += 1
                if self._responses > DUPLEX_RESPONSES:
                    spawn(stop())

        async def on_enter(self):
            self.duplex_session.on("openai_server_event_received", self._count)
            await super().on_enter()

    def build(architecture, label, question):
        if architecture == "duplex":
            return BoundedDuplex(label, question)
        return BoundedCascade(label, question)

    return build
