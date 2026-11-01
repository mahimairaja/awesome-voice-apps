"""Maintainer-owned adapter for the standalone flight rebooking demo."""

import importlib.util
from pathlib import Path

from livekit.agents import inference, tts
from livekit.plugins import openai

# Keep the contributed demo self-contained; load it by path so its `agent`
# module never shadows the coffee demo's `agent` module.
_demo = Path(__file__).resolve().parents[1] / "demos" / "flight-rebooking"
_spec = importlib.util.spec_from_file_location("playground_rebook_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
FlightRebooker = _module.FlightRebooker
build_llm = _module.build_llm


class FlakyTTS(inference.TTS):
    """The primary voice on LiveKit Inference, failing fast while its outage lasts."""

    def __init__(self, *, outage, **kwargs) -> None:
        super().__init__(**kwargs)
        self._outage = outage

    def stream(self, **kwargs):
        self._outage.check("tts")
        return super().stream(**kwargs)

    def synthesize(self, text, **kwargs):
        self._outage.check("tts")
        return super().synthesize(text, **kwargs)


def build_tts(outage) -> tts.FallbackAdapter:
    """The demo's failover voice, with the primary on Inference (no Cartesia key)."""
    return tts.FallbackAdapter(
        [
            FlakyTTS(outage=outage, model="cartesia/sonic-3"),
            openai.TTS(model="tts-1", voice="alloy"),
        ],
        max_retry_per_tts=0,
    )
