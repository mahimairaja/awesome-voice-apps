"""Run the playground's speech models on LiveKit Inference.

The demos in demos/ build provider plugin clients (Deepgram, Cartesia, OpenAI) so
each one runs on its own with that provider's key. The hosted worker holds no
Deepgram or Cartesia key: it turns each of those clients into the LiveKit
Inference client for the same model, voice and options. LiveKit bills the
project, and VoiceGateway prices the usage as provider "livekit".

OpenAI language models move too. Clients this module cannot express on Inference
(a custom endpoint, OpenAI's own speech models, a demo's subclass) stay as they
are and use the worker's OpenAI key.
"""

import os

from livekit.agents import NOT_GIVEN, inference
from livekit.plugins import cartesia, deepgram, openai

from model_menu import PLACEHOLDER

# PLACEHOLDER lets a demo construct its own Deepgram or Cartesia client before the
# hosted layer replaces it. The value is never sent: any such client left
# unconverted fails the call before it opens a stream (see check).
PLACEHOLDER_KEYS = ("DEEPGRAM_API_KEY", "CARTESIA_API_KEY")
OPENAI_URL = "https://api.openai.com/v1"
# Inference model names that differ from the plugin's.
DEEPGRAM_STT = {"nova-2-general": "nova-2"}
# The Deepgram speech models LiveKit Inference serves; any other stays a plugin client.
DEEPGRAM_INFERENCE = frozenset(
    {"nova-3", "nova-3-medical", "nova-2", "nova-2-medical", "nova-2-conversationalai"}
    | {"nova-2-phonecall"}
)
DEEPGRAM_TTS = {"aura-2": "deepgram/aura-2", "aura": "deepgram/aura"}
# Deepgram's default; Inference would otherwise use its own.
DEEPGRAM_ENDPOINTING_MS = 25


def install_placeholders() -> None:
    """Let demos build Deepgram and Cartesia clients without those providers' keys."""
    for key in PLACEHOLDER_KEYS:
        if not os.environ.get(key):
            os.environ[key] = PLACEHOLDER


def placeholder(key: str) -> bool:
    return os.environ.get(key) in (None, "", PLACEHOLDER)


def _set(value) -> bool:
    return value is not NOT_GIVEN and value is not None and value != []


def _deepgram_stt(client: deepgram.STT):
    o = client._opts
    model = DEEPGRAM_STT.get(o.model, o.model)
    if model not in DEEPGRAM_INFERENCE:
        return client
    extra = {"endpointing": o.endpointing_ms or DEEPGRAM_ENDPOINTING_MS}
    for name in ("keyterm", "keywords", "replace", "search", "redact"):
        if _set(getattr(o, name, None)):
            extra[name] = list(getattr(o, name))
    for name in ("smart_format", "numerals", "profanity_filter", "dictation"):
        if getattr(o, name, False):
            extra[name] = True
    return inference.STT(model=f"deepgram/{model}", language=o.language or "en", extra_kwargs=extra)


def _deepgram_flux(client: deepgram.STTv2) -> inference.STT:
    o = client._opts
    extra = {
        name: getattr(o, name)
        for name in ("eager_eot_threshold", "eot_threshold", "eot_timeout_ms")
        if _set(getattr(o, name))
    }
    if o.keyterm:
        extra["keyterm"] = list(o.keyterm)
    return inference.STT(
        model=f"deepgram/{o.model}", language=o.language or "en", extra_kwargs=extra
    )


def _deepgram_tts(client: deepgram.TTS) -> inference.TTS:
    # Plugin ids read aura-2-<voice>-<lang>, such as aura-2-thalia-en.
    parts = client._opts.model.split("-")
    family = "aura-2" if parts[:2] == ["aura", "2"] else "aura"
    voice = parts[2] if family == "aura-2" else parts[1]
    return inference.TTS(model=DEEPGRAM_TTS[family], voice=voice)


def _cartesia_stt(client: cartesia.STT) -> inference.STT:
    extra = {"keyterm": list(client._keyterm)} if _set(client._keyterm) else NOT_GIVEN
    language = client._language if isinstance(client._language, str) else "en"
    return inference.STT(model=f"cartesia/{client._model}", language=language, extra_kwargs=extra)


def _cartesia_tts(client: cartesia.TTS) -> inference.TTS:
    o = client._opts
    extra = {}
    if _set(o.speed):
        extra["speed"] = o.speed
    if _set(o.emotion):
        emotion = o.emotion
        extra["emotion"] = emotion[0] if isinstance(emotion, list) else emotion
    if _set(o.volume):
        extra["volume"] = o.volume
    voice = o.voice if isinstance(o.voice, str) else NOT_GIVEN
    return inference.TTS(
        model=f"cartesia/{o.model}",
        voice=voice,
        language=o.language or NOT_GIVEN,
        extra_kwargs=extra or NOT_GIVEN,
    )


def _openai_llm(client: openai.LLM):
    if str(client._client.base_url).rstrip("/") != OPENAI_URL:
        return client  # A custom endpoint (a self-hosted model) keeps its own client.
    o = client._opts
    extra = {
        name: getattr(o, name)
        for name in ("max_completion_tokens", "temperature", "top_p", "parallel_tool_calls")
        if _set(getattr(o, name))
    }
    return inference.LLM(model=f"openai/{o.model}", extra_kwargs=extra or None)


# Exact types only: a demo's subclass changes behavior that a swap would drop.
CONVERTERS = {
    deepgram.STT: _deepgram_stt,
    deepgram.STTv2: _deepgram_flux,
    deepgram.TTS: _deepgram_tts,
    cartesia.STT: _cartesia_stt,
    cartesia.TTS: _cartesia_tts,
    openai.LLM: _openai_llm,
}


def convert(client):
    """The Inference client for a plugin client, or the client itself when it has none."""
    converter = CONVERTERS.get(type(client))
    return converter(client) if converter else client


def check(client) -> None:
    """Refuse a Deepgram or Cartesia client that would run on the placeholder key."""
    needs = {"deepgram": "DEEPGRAM_API_KEY", "cartesia": "CARTESIA_API_KEY"}
    for provider, key in needs.items():
        if type(client).__module__.startswith(f"livekit.plugins.{provider}") and placeholder(key):
            raise RuntimeError(f"{type(client).__name__} from {provider} needs {key}")
