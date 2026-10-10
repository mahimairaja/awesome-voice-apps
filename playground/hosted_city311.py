"""Maintainer-owned adapter for the standalone bilingual 311 demo."""

import importlib.util
import sys
import types
from pathlib import Path

# The hosted worker uses silero VAD only; the standalone demo also loads the
# multilingual turn detector, which the hosted image does not install. Give that
# import an inert placeholder for the load only.
_STUBS = ("livekit.plugins.turn_detector", "livekit.plugins.turn_detector.multilingual")


def _load():
    demo = Path(__file__).resolve().parents[1] / "demos" / "city-311"
    spec = importlib.util.spec_from_file_location("playground_city311_agent", demo / "agent.py")
    module = importlib.util.module_from_spec(spec)
    added = []
    for name in _STUBS:
        if name not in sys.modules:
            stub = types.ModuleType(name)
            stub.MultilingualModel = None
            sys.modules[name] = stub
            added.append(name)
    try:
        spec.loader.exec_module(module)
    finally:
        for name in added:
            sys.modules.pop(name, None)
    return module


_module = _load()
City311Agent = _module.City311Agent
LanguageRouter = _module.LanguageRouter
initial_state = _module.initial_state
make_stt = _module.make_stt
make_tts = _module.make_tts
publish_city311 = _module.publish_city311

GREETING = (
    "Open with exactly 'Bellerive 311, bonjour, hi.' Then say in English, in one short "
    "sentence, that this is a simulation and they can report a pothole or ask about "
    "garbage day in English or French."
)
