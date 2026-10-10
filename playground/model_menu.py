"""Models a visitor may switch a demo to, by layer (the switcher on the demo page).

The site lists the same ids (mahimai.ca src/config/playground-models.ts) and signs
the visitor's pick into the agent dispatch. The worker trusts only ids it finds
here, so a forged dispatch can never name a model or provider we did not choose.

Every model here must be priced by VoiceGateway (an unpriced row stops the call)
and billed by a provider PlaygroundSink accepts.
"""

from collections.abc import Callable

from livekit.plugins import cartesia, deepgram, openai

LAYERS = ("stt", "llm", "tts")
# What every demo on the plain stack runs unless the visitor picks otherwise.
DEFAULT = {"stt": "deepgram-nova-3", "llm": "openai-gpt-4o-mini", "tts": "cartesia-sonic-3"}

MODELS: dict[str, dict[str, Callable[[], object]]] = {
    "stt": {
        "deepgram-nova-3": lambda: deepgram.STT(model="nova-3"),
        "openai-gpt-4o-mini-transcribe": lambda: openai.STT(
            model="gpt-4o-mini-transcribe", use_realtime=True
        ),
    },
    "llm": {
        "openai-gpt-4o-mini": lambda: openai.LLM(
            model="gpt-4o-mini", max_completion_tokens=180, max_retries=0, store=False
        ),
        "openai-gpt-4.1-mini": lambda: openai.LLM(
            model="gpt-4.1-mini", max_completion_tokens=180, max_retries=0, store=False
        ),
        "openai-gpt-4.1-nano": lambda: openai.LLM(
            model="gpt-4.1-nano", max_completion_tokens=180, max_retries=0, store=False
        ),
    },
    "tts": {
        "cartesia-sonic-3": lambda: cartesia.TTS(model="sonic-3"),  # sonic-2 is being retired
        "deepgram-aura-2": lambda: deepgram.TTS(model="aura-2-thalia-en"),
        "openai-tts-1": lambda: openai.TTS(model="tts-1", voice="alloy"),
    },
}


def parse_choice(raw) -> dict[str, str]:
    """The valid part of a dispatched choice; anything else keeps the demo's default."""
    if not isinstance(raw, dict):
        return {}
    return {
        layer: raw[layer]
        for layer in LAYERS
        if isinstance(raw.get(layer), str) and raw[layer] in MODELS[layer]
    }


def build(choice: dict[str, str]) -> dict:
    """Fresh clients for each chosen layer, so metrics never mix between calls."""
    return {layer: MODELS[layer][model_id]() for layer, model_id in choice.items()}
