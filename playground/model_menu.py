"""Models a visitor may switch a demo to, by layer (the switcher on the demo page).

Two parts:

- MODELS, the catalog: every model the hosted playground can run. Maintainers add
  to it when a provider gives credits. Every model here must be priced by
  VoiceGateway (an unpriced row stops the call) and billed by a provider
  PlaygroundSink accepts.
- demos/<folder>/models.toml, optional and contributor-owned: which catalog
  models suit that demo, best first. Those lead the menu as the demo's picks and
  every other general model follows, so a visitor can still try any of them. A
  medical line can put a medical STT first, or list a single model to lock that
  layer. SPECIALIST models only appear where a demo asks for them.

`python playground/model_menu.py` writes playground/model-menus.json, the merged
menus. The worker reads that file, the site fetches it at build time, and a test
fails when it is stale. The site signs the visitor's pick into the agent
dispatch; the worker trusts only ids on that demo's menu, so a forged dispatch can
never name a model or provider we did not choose.
"""

import importlib
import json
import os
import sys
import tomllib
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MENUS = Path(__file__).resolve().with_name("model-menus.json")
LAYERS = ("stt", "llm", "tts")
# What a demo runs when neither its models.toml nor the visitor picks otherwise.
DEFAULT = {"stt": "deepgram-nova-3", "llm": "openai-gpt-4o-mini", "tts": "cartesia-sonic-3"}
LLM_LIMITS = {"max_completion_tokens": 180, "max_retries": 0, "store": False}

# Each id is (livekit plugin, class, keyword arguments). Plugins import only when a
# call builds a client, so `python playground/model_menu.py --check` needs no install.
# Within a layer, ids are grouped alphabetically by provider, newest model first: a
# demo with no models.toml shows them in this order, with DEFAULT first.
# Every model the installed plugin offers that VoiceGateway prices, except dated
# snapshots and retired generations. Deepgram's nova-3 multilingual mode is left
# out because it is priced as plain nova-3, and gpt-4o-mini-tts because
# VoiceGateway has no price for it.
NOVA_2 = ("atc", "automotive", "conversationalai", "drivethru", "finance", "meeting")
NOVA_2 += ("medical", "phonecall", "video", "voicemail")
OPENAI_LLMS = ("gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.2", "gpt-5.1", "gpt-5")
OPENAI_LLMS += ("gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano")
OPENAI_LLMS += ("gpt-4o", "gpt-4o-mini")
# The plugin's Agent STT runs only linden-1 (it swaps any other model name for it), so
# Melia-1, Enhanced and Standard are not reachable from LiveKit yet.
SPEECHMATICS_VOICES = ("sarah", "theo", "megan", "jack")

MODELS: dict[str, dict[str, tuple[str, str, dict]]] = {
    "stt": {
        "cartesia-ink-2": ("cartesia", "STT", {"model": "ink-2"}),
        "cartesia-ink-whisper": ("cartesia", "STT", {"model": "ink-whisper"}),
        "deepgram-nova-3": ("deepgram", "STT", {"model": "nova-3"}),
        "deepgram-nova-3-medical": ("deepgram", "STT", {"model": "nova-3-medical"}),
        "deepgram-flux": ("deepgram", "STTv2", {"model": "flux-general-en"}),
        "deepgram-nova-2": ("deepgram", "STT", {"model": "nova-2-general"}),
        **{
            f"deepgram-nova-2-{domain}": ("deepgram", "STT", {"model": f"nova-2-{domain}"})
            for domain in NOVA_2
        },
        "deepgram-enhanced": ("deepgram", "STT", {"model": "enhanced-general"}),
        "deepgram-base": ("deepgram", "STT", {"model": "base"}),
        "openai-gpt-live-transcribe": ("openai", "STT", {"model": "gpt-live-transcribe"}),
        # Served only by the transcriptions endpoint; the session VAD segments it.
        "openai-gpt-transcribe": ("openai", "STT", {"model": "gpt-transcribe"}),
        "openai-gpt-4o-transcribe": (
            "openai",
            "STT",
            {"model": "gpt-4o-transcribe", "use_realtime": True},
        ),
        "openai-gpt-4o-mini-transcribe": (
            "openai",
            "STT",
            {"model": "gpt-4o-mini-transcribe", "use_realtime": True},
        ),
        "openai-gpt-realtime-whisper": ("openai", "STT", {"model": "gpt-realtime-whisper"}),
        "openai-whisper-1": ("openai", "STT", {"model": "whisper-1", "use_realtime": True}),
        # Sponsored by Speechmatics. The service closes its own turns ("vad" mode), so
        # the plugin loads no second Silero model per call; the global endpoint routes
        # each call to the nearest region. Priced in hosted.PlaygroundSink.
        "speechmatics-linden-1": (
            "speechmatics",
            "STT",
            {
                "model": "linden-1",
                "turn_detection_mode": "vad",
                "enable_diarization": False,
                "base_url": "wss://global.rt.speechmatics.com/v2/agent",
            },
        ),
    },
    # The plugin sets reasoning to none or minimal for gpt-5 models, so the
    # completion cap is spent on the reply.
    "llm": {
        f"openai-{model}": ("openai", "LLM", {"model": model, **LLM_LIMITS})
        for model in OPENAI_LLMS
    },
    "tts": {
        # sonic-2 is being retired.
        "cartesia-sonic-3": ("cartesia", "TTS", {"model": "sonic-3"}),
        "cartesia-sonic-turbo": ("cartesia", "TTS", {"model": "sonic-turbo"}),
        "deepgram-aura-2": ("deepgram", "TTS", {"model": "aura-2-thalia-en"}),
        "deepgram-aura": ("deepgram", "TTS", {"model": "aura-asteria-en"}),
        "openai-tts-1": ("openai", "TTS", {"model": "tts-1", "voice": "alloy"}),
        "openai-tts-1-hd": ("openai", "TTS", {"model": "tts-1-hd", "voice": "alloy"}),
        # Sponsored by Speechmatics: its preview TTS, one model with four English voices.
        # Priced in hosted.PlaygroundSink.
        **{
            f"speechmatics-{voice}": ("speechmatics", "TTS", {"voice": voice})
            for voice in SPEECHMATICS_VOICES
        },
    },
}


# Sponsor keys the worker may not hold yet. Until a key is set in Railway, that
# provider's models are skipped: the demo runs its next model instead of failing.
OPTIONAL_KEYS = {"speechmatics": "SPEECHMATICS_API_KEY"}

# Domain models only a demo's models.toml can offer; a general demo never shows them.
SPECIALIST = frozenset({"deepgram-nova-2-medical", "deepgram-nova-3-medical"})


def default_menu() -> dict[str, list[str]]:
    return {
        layer: [
            DEFAULT[layer],
            *(i for i in MODELS[layer] if i != DEFAULT[layer] and i not in SPECIALIST),
        ]
        for layer in LAYERS
    }


def read_priority(path: Path) -> dict[str, list[str]]:
    """A demo's models.toml, checked against the catalog. Raises ValueError when invalid."""
    data = tomllib.loads(path.read_text())
    unknown = set(data) - set(LAYERS)
    if unknown:
        raise ValueError(f"{path}: unknown layer {sorted(unknown)}; use {list(LAYERS)}")
    menu = default_menu()
    for layer, ids in data.items():
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
            raise ValueError(f"{path}: {layer} must be a non-empty list of model ids")
        if len(set(ids)) != len(ids):
            raise ValueError(f"{path}: {layer} lists a model twice")
        missing = [i for i in ids if i not in MODELS[layer]]
        if missing:
            raise ValueError(
                f"{path}: {layer} has {missing}, not in the catalog. "
                f"Pick from {sorted(MODELS[layer])}, or ask a maintainer to add one."
            )
        # One id locks the layer; otherwise the picks lead and the rest follow.
        menu[layer] = ids if len(ids) == 1 else ids + [i for i in menu[layer] if i not in ids]
    return menu


def read_picks(path: Path) -> dict[str, list[str]]:
    """The models a demo's models.toml puts first, shown as its picks on the page."""
    data = tomllib.loads(path.read_text())
    return {layer: data.get(layer, [DEFAULT[layer]]) for layer in LAYERS}


def collect(root: Path = ROOT) -> dict:
    """The merged menus: the catalog default plus every demo that has a models.toml."""
    paths = sorted((root / "demos").glob("*/models.toml"))
    return {
        "layers": list(LAYERS),
        "default": default_menu(),
        "demos": {path.parent.name: read_priority(path) for path in paths},
        # What each demo puts first, for the page's "picked for this use case" group.
        "picks": {path.parent.name: read_picks(path) for path in paths},
    }


@cache
def menus() -> dict:
    return json.loads(MENUS.read_text())


def menu_for(source: str) -> dict[str, list[str]]:
    """The menu for a demo folder; the default menu when it has no models.toml."""
    data = menus()
    return data["demos"].get(source, data["default"])


def runnable(layer: str, model_id: str) -> bool:
    """False when the model's provider needs a key this worker does not hold."""
    key = OPTIONAL_KEYS.get(MODELS[layer][model_id][0])
    return key is None or bool(os.environ.get(key))


def parse_choice(raw, source: str = "") -> dict[str, str]:
    """The valid part of a dispatched choice; anything else keeps the demo's default."""
    if not isinstance(raw, dict):
        return {}
    menu = menu_for(source)
    return {
        layer: raw[layer]
        for layer in LAYERS
        if isinstance(raw.get(layer), str)
        and raw[layer] in menu[layer]
        and runnable(layer, raw[layer])
    }


def defaults(source: str = "") -> dict[str, str]:
    """The first model on each layer's menu that this worker can run."""
    return {
        layer: next((i for i in ids if runnable(layer, i)), ids[0])
        for layer, ids in menu_for(source).items()
    }


def client(layer: str, model_id: str):
    plugin, name, options = MODELS[layer][model_id]
    module = importlib.import_module(f"livekit.plugins.{plugin}")
    if "turn_detection_mode" in options:
        # The Speechmatics plugin reads `.value` off its own enum; a bare string fails
        # when the stream opens, not when the client is built.
        mode = module.TurnDetectionMode(options["turn_detection_mode"])
        options = {**options, "turn_detection_mode": mode}
    return getattr(module, name)(**options)


def build(choice: dict[str, str]) -> dict:
    """Fresh clients for each chosen layer, so metrics never mix between calls."""
    return {layer: client(layer, model_id) for layer, model_id in choice.items()}


def main(argv: list[str]) -> int:
    try:
        merged = collect()
    except ValueError as problem:
        print(problem, file=sys.stderr)
        return 1
    text = json.dumps(merged, indent=2) + "\n"
    if "--check" in argv:
        if MENUS.read_text() != text:
            print("playground/model-menus.json is stale: run python playground/model_menu.py")
            return 1
        print("Model menus are valid and current.")
        return 0
    MENUS.write_text(text)
    print(f"Wrote {MENUS.relative_to(ROOT)} ({len(merged['demos'])} demos with models.toml)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
