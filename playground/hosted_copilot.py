"""Maintainer-owned adapter for the standalone sales copilot demo.

The hosted call keeps the demo's prospect, playbook and copilot unchanged. It
gives the prospect a sonic-3 voice (sonic-2 is being retired), scales the
copilot's limits to the call's allowance, and prices the copilot's own OpenAI
requests, which VoiceGateway never sees because they bypass the session.
"""

import importlib.util
import sys
import types
from decimal import Decimal
from pathlib import Path

from livekit.plugins import cartesia

_demo = Path(__file__).resolve().parents[1] / "demos" / "sales-copilot"


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their module through sys.modules; the names are unique.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo() -> tuple[types.ModuleType, types.ModuleType]:
    # The demo imports `copilot` by name; give it the demo's own copy only
    # while it loads, so no other demo ever resolves that name.
    copilot = _load("playground_copilot_core", _demo / "copilot.py")
    previous = sys.modules.get("copilot")
    sys.modules["copilot"] = copilot
    try:
        return copilot, _load("playground_copilot_agent", _demo / "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("copilot", None)
        else:
            sys.modules["copilot"] = previous


_core, _module = _load_demo()
Prospect = _module.Prospect
GREETING = (
    "First say, in one short sentence, that this is a sales role-play with made-up "
    "companies and nothing is sold. Then: " + _module.GREETING
)

# Published OpenAI prices, USD per token.
PRICES = {
    _core.EMBED_MODEL: (Decimal("0.02") / 1_000_000, Decimal(0)),
    _core.LINE_MODEL: (Decimal("0.15") / 1_000_000, Decimal("0.60") / 1_000_000),
}
# Copilot work in a full two-minute call; shorter calls scale down.
MAX_LINES = 14
MAX_LOOKUPS = 24


def cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Price one copilot request. An unknown model is an error, never free."""
    per_in, per_out = PRICES[model]
    return Decimal(max(0, input_tokens)) * per_in + Decimal(max(0, output_tokens)) * per_out


def limits(seconds: int) -> dict:
    scale = seconds / 120
    return {
        "max_lines": max(1, int(MAX_LINES * scale)),
        "max_lookups": max(1, int(MAX_LOOKUPS * scale)),
    }


def voice() -> cartesia.TTS:
    """A fresh per-call client for the prospect."""
    return cartesia.TTS(model="sonic-3", voice=_module.PROSPECT_VOICE)
