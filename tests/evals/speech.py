"""Local speech models for the eval caller, so a run calls no paid API.

DeepEval speaks each scripted caller line with a TTS model and transcribes the
agent with an STT model. Both run here on CPU: Kokoro for the caller's voice
and faster-whisper as the fallback when the agent publishes no transcript
(LiveKit agents usually do, on ``lk.transcription``). The simulator also needs
an LLM object, but the scripted graph never calls it, so ``NoLLM`` refuses.
"""

from __future__ import annotations

import asyncio
import io
import os
import wave
from pathlib import Path

import numpy as np
from deepeval.models import DeepEvalBaseLLM
from deepeval.models.base_model import DeepEvalBaseSTT, DeepEvalBaseTTS
from deepeval.test_case import Audio

MODELS = Path(os.environ.get("EVAL_MODELS_DIR", Path(__file__).parent / ".models"))
KOKORO_MODEL = "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = "voices-v1.0.bin"


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    """Mono float samples in [-1, 1] as 16-bit PCM WAV."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return out.getvalue()


def wav_samples(audio: Audio) -> tuple[np.ndarray, int]:
    """Decode a WAV ``Audio`` to mono float32 samples and its rate."""
    with wave.open(io.BytesIO(audio.get_bytes()), "rb") as wav:
        rate = wav.getframerate()
        channels = wav.getnchannels()
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return pcm.astype(np.float32) / 32768.0, rate


class KokoroTTS(DeepEvalBaseTTS):
    """Kokoro 82M (int8 ONNX) on CPU. Free, and fast enough for one caller."""

    sample_rate = 24000

    def __init__(self, voice: str = "af_heart", speed: float = 1.0):
        self.voice = voice
        self.speed = speed
        super().__init__(model="kokoro-v1.0")

    def load_model(self):
        from kokoro_onnx import Kokoro

        return Kokoro(str(MODELS / KOKORO_MODEL), str(MODELS / KOKORO_VOICES))

    def synthesize(self, text: str, *args, voice: str | None = None, **kwargs):
        samples, rate = self.model.create(
            text, voice=voice or self.voice, speed=self.speed, lang="en-us"
        )
        audio = Audio.from_bytes(
            wav_bytes(samples, rate),
            "audio/wav",
            sampleRate=rate,
            encoding="wav",
            duration=len(samples) / rate,
        )
        return audio, 0.0

    async def a_synthesize(self, text: str, *args, voice: str | None = None, **kwargs):
        return await asyncio.to_thread(self.synthesize, text, voice=voice)

    def synthesis_cost(self, text: str) -> float:
        return 0.0

    def get_model_name(self) -> str:
        return "kokoro-v1.0 (local)"


class WhisperSTT(DeepEvalBaseSTT):
    """faster-whisper on CPU, loaded only if the agent sends no transcript."""

    def __init__(self, size: str = "base.en"):
        self.size = size
        self._whisper = None
        super().__init__(model=f"whisper-{size}")

    def load_model(self):
        return self

    def _model(self):
        if self._whisper is None:
            from faster_whisper import WhisperModel

            self._whisper = WhisperModel(self.size, device="cpu", compute_type="int8")
        return self._whisper

    def transcribe(self, audio: Audio, *args, **kwargs):
        samples, rate = wav_samples(audio)
        if rate != 16000 and len(samples):
            # Linear resample is plenty for a fallback transcript.
            positions = np.linspace(0, len(samples) - 1, int(len(samples) * 16000 / rate))
            samples = np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)
        segments, _ = self._model().transcribe(samples, language="en", beam_size=1)
        return " ".join(s.text.strip() for s in segments).strip(), 0.0

    async def a_transcribe(self, audio: Audio, *args, **kwargs):
        return await asyncio.to_thread(self.transcribe, audio)

    def get_model_name(self) -> str:
        return f"faster-whisper {self.size} (local)"


class NoLLM(DeepEvalBaseLLM):
    """Stands in for the simulator model. The scripted caller never needs it."""

    def __init__(self):
        super().__init__(model="none")

    def load_model(self):
        return self

    def generate(self, *args, **kwargs) -> str:
        raise RuntimeError("The scripted caller must not call an LLM.")

    async def a_generate(self, *args, **kwargs) -> str:
        raise RuntimeError("The scripted caller must not call an LLM.")

    def get_model_name(self) -> str:
        return "none (scripted caller)"
