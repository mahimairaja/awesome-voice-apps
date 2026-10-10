"""Maintainer-owned adapter for the standalone furnace repair demo."""

import importlib.util
import sys
import types
from pathlib import Path

_demo = Path(__file__).resolve().parents[1] / "demos" / "furnace-repair"


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo() -> types.ModuleType:
    # The demo imports `turns` by name; give it the demo's own copy only while
    # it loads, so no other demo ever resolves that name.
    turns = _load("playground_furnace_turns", _demo / "turns.py")
    previous = sys.modules.get("turns")
    sys.modules["turns"] = turns
    try:
        return _load("playground_furnace_agent", _demo / "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("turns", None)
        else:
            sys.modules["turns"] = previous


_module = _load_demo()
FurnaceLine = _module.FurnaceLine
COMPANY = _module.COMPANY

# The local v1-mini detector adds about 250 MB of resident memory to the worker
# (measured with livekit-agents 1.8.5: 217 MB idle, 477 MB once it loads), which
# does not fit the 500 MB service. The hosted call uses the full v1 model on
# LiveKit Inference with the worker's existing LiveKit keys, and never loads the
# local model: if Inference is unreachable, turns commit on the endpointing
# delay and the panel says the detector is off.
DETECTOR_OPTIONS = {"version": "v1", "local_fallback": False}


def initial_state() -> dict:
    return _module.new_ticket()


def publish_furnace(room, data: dict) -> None:
    _module.publish_ticket(room, data)
