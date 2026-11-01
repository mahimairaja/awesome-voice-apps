"""Versioned demo configs from mahimai.ca, and the LiveKit Inference clients they name.

The site stores each demo's config as an immutable version (instructions,
greeting, and LiveKit Inference model ids) and signs the live version number
into the call's dispatch. The worker fetches that exact version once, while it
admits the call, so a call never changes config halfway and a bad config
rejects the call before any inference starts.

A config only adjusts a demo that already runs here: its tools and UI events
stay in the demo's own code. Models are LiveKit Inference ids such as
``deepgram/nova-3``, ``openai/gpt-4.1-mini`` or ``cartesia/sonic-3:<voice>``,
billed to the LiveKit project, so no provider key is needed for them.
"""

import re
from dataclasses import dataclass, field

from livekit.agents import NOT_GIVEN, inference

ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
# provider/model, optionally :voice. The same pattern the site checks on publish.
MODEL = re.compile(r"^[a-z0-9-]+/[A-Za-z0-9._-]+(:[A-Za-z0-9._-]+)?$")
FIELDS = {"kind", "module", "toolset", "instructions", "greeting", "language", "models"}
LAYERS = ("stt", "llm", "tts")
LIMITS = {"instructions": 16000, "greeting": 1000, "language": 16}


@dataclass(frozen=True)
class DemoConfig:
    version: int
    instructions: str | None = None
    greeting: str | None = None
    language: str | None = None
    models: dict[str, str] = field(default_factory=dict)


def _text(raw: dict, name: str) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > LIMITS[name]:
        raise ValueError(f"Invalid {name}")
    return value


def parse(raw, demo: str, version: int) -> DemoConfig:
    """Check a fetched config again; the worker trusts nothing it did not validate."""
    if not isinstance(raw, dict) or set(raw) - FIELDS:
        raise ValueError("Invalid config")
    if raw.get("kind") not in ("config", "module"):
        raise ValueError("Invalid config kind")
    # Tools come from the demo's own code, so a config may only name that demo.
    for name in ("module", "toolset"):
        value = raw.get(name)
        if value is not None and (not isinstance(value, str) or not ID.match(value)):
            raise ValueError(f"Invalid {name}")
        if value is not None and value != demo:
            raise ValueError(f"{name} {value!r} does not run as {demo!r}")
    models = raw.get("models")
    if models is None:
        models = {}
    if not isinstance(models, dict) or set(models) - set(LAYERS):
        # A realtime model needs a realtime demo; none of those take a config yet.
        raise ValueError("Invalid models")
    for layer, model in models.items():
        if not isinstance(model, str) or not MODEL.match(model):
            raise ValueError(f"Invalid {layer} model")
    if ":" in models.get("stt", "") or ":" in models.get("llm", ""):
        raise ValueError("Only a TTS model takes a voice")
    return DemoConfig(
        version=version,
        instructions=_text(raw, "instructions"),
        greeting=_text(raw, "greeting"),
        language=_text(raw, "language"),
        models=dict(models),
    )


def client(layer: str, model: str, language: str | None = None):
    """A fresh LiveKit Inference client, so metrics never mix between calls."""
    if layer == "stt":
        return inference.STT(model=model, language=language or "en")
    if layer == "llm":
        return inference.LLM(model=model)
    name, _, voice = model.partition(":")
    return inference.TTS(model=name, voice=voice or NOT_GIVEN, language=language or NOT_GIVEN)


def build(config: DemoConfig) -> dict:
    return {layer: client(layer, m, config.language) for layer, m in config.models.items()}
