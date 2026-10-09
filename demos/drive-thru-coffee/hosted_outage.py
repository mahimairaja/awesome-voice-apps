"""Maintainer-owned adapter for the standalone storm outage line demo.

The demo already runs on Deepgram, OpenAI and Cartesia, so nothing is swapped
but the clients HostedGuard owns. Its agent imports its helper modules by name;
they are given unique names here and exposed only while the agent loads, so no
other demo ever resolves `phone_line` or `scoring`.
"""

import importlib.util
import sys
import types
from pathlib import Path

_demo = Path(__file__).resolve().parents[1] / "storm-outage-line"
_HELPERS = ("phone_line", "scoring")


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their module through sys.modules; the names are unique.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo() -> types.ModuleType:
    helpers = {name: _load(f"playground_outage_{name}", _demo / f"{name}.py") for name in _HELPERS}
    previous = {name: sys.modules.get(name) for name in _HELPERS}
    sys.modules.update(helpers)
    try:
        return _load("playground_outage_agent", _demo / "agent.py")
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


_module = _load_demo()
OutageLine = _module.OutageLine
GREETING = _module.GREETING
initial_state = _module.initial_state
outage_stt = _module.outage_stt
