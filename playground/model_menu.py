"""Models a visitor may switch a demo to, by layer (the switcher on the demo page).

Two parts:

- MODELS, the catalog: every model the hosted playground can run. Maintainers add
  to it when a provider gives credits. Every model here must be priced by
  VoiceGateway (an unpriced row stops the call) and billed by a provider
  PlaygroundSink accepts.
- demos/<folder>/models.toml, optional and contributor-owned: which catalog
  models suit that demo, best first. A medical line can put a medical STT first,
  or list a single STT to lock it. Layers it leaves out offer the catalog's
  general models (SPECIALIST ones only appear where a demo asks for them).

`python playground/model_menu.py` writes playground/model-menus.json, the merged
menus. The worker reads that file, the site fetches it at build time, and a test
fails when it is stale. The site signs the visitor's pick into the agent
dispatch; the worker trusts only ids on that demo's menu, so a forged dispatch can
never name a model or provider we did not choose.
"""

import importlib
import json
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
# Within a layer, ids are listed alphabetically by provider: a demo with no
# models.toml shows them in this order, with DEFAULT first.
MODELS: dict[str, dict[str, tuple[str, str, dict]]] = {
    "stt": {
        "deepgram-nova-3": ("deepgram", "STT", {"model": "nova-3"}),
        "deepgram-nova-3-medical": ("deepgram", "STT", {"model": "nova-3-medical"}),
        "openai-gpt-4o-mini-transcribe": (
            "openai",
            "STT",
            {"model": "gpt-4o-mini-transcribe", "use_realtime": True},
        ),
    },
    "llm": {
        "openai-gpt-4.1-mini": ("openai", "LLM", {"model": "gpt-4.1-mini", **LLM_LIMITS}),
        "openai-gpt-4.1-nano": ("openai", "LLM", {"model": "gpt-4.1-nano", **LLM_LIMITS}),
        "openai-gpt-4o-mini": ("openai", "LLM", {"model": "gpt-4o-mini", **LLM_LIMITS}),
    },
    "tts": {
        # sonic-2 is being retired.
        "cartesia-sonic-3": ("cartesia", "TTS", {"model": "sonic-3"}),
        "deepgram-aura-2": ("deepgram", "TTS", {"model": "aura-2-thalia-en"}),
        "openai-tts-1": ("openai", "TTS", {"model": "tts-1", "voice": "alloy"}),
    },
}


# Domain models only a demo's models.toml can offer; a general demo never shows them.
SPECIALIST = frozenset({"deepgram-nova-3-medical"})


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
        menu[layer] = ids
    return menu


def collect(root: Path = ROOT) -> dict:
    """The merged menus: the catalog default plus every demo that has a models.toml."""
    demos = {
        path.parent.name: read_priority(path)
        for path in sorted((root / "demos").glob("*/models.toml"))
    }
    return {"layers": list(LAYERS), "default": default_menu(), "demos": demos}


@cache
def menus() -> dict:
    return json.loads(MENUS.read_text())


def menu_for(source: str) -> dict[str, list[str]]:
    """The menu for a demo folder; the default menu when it has no models.toml."""
    data = menus()
    return data["demos"].get(source, data["default"])


def parse_choice(raw, source: str = "") -> dict[str, str]:
    """The valid part of a dispatched choice; anything else keeps the demo's default."""
    if not isinstance(raw, dict):
        return {}
    menu = menu_for(source)
    return {
        layer: raw[layer]
        for layer in LAYERS
        if isinstance(raw.get(layer), str) and raw[layer] in menu[layer]
    }


def defaults(source: str = "") -> dict[str, str]:
    return {layer: ids[0] for layer, ids in menu_for(source).items()}


def client(layer: str, model_id: str):
    plugin, name, options = MODELS[layer][model_id]
    module = importlib.import_module(f"livekit.plugins.{plugin}")
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
