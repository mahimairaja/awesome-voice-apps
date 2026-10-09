"""Maintainer-owned adapter for the standalone pronunciation coach demo."""

import importlib.util
import sys
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's. Its `import coach` sees the demo's own copy only while it loads.
_demo = Path(__file__).resolve().parents[1] / "pronunciation-coach"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _demo / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo():
    coach = _load("playground_pronounce_coach", "coach.py")
    previous = sys.modules.get("coach")
    sys.modules["coach"] = coach
    try:
        agent = _load("playground_pronounce_agent", "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("coach", None)
        else:
            sys.modules["coach"] = previous
    return coach, agent


coach, _module = _load_demo()
PronunciationCoach = _module.PronunciationCoach
make_stt = _module.make_stt
make_tts = _module.make_tts
publish_coach = _module.publish_coach

GREETING = _module.GREETING
HOSTED_INSTRUCTIONS = (
    " This is a public demo with a two-minute limit: keep every reply under twenty words."
)


def instructions() -> str:
    return _module.INSTRUCTIONS + HOSTED_INSTRUCTIONS


def initial_state() -> dict:
    return coach.initial_state()
