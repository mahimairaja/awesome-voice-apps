"""Maintainer-owned adapter for the standalone post-op check-in demo."""

import importlib.util
import sys
from pathlib import Path

from livekit.plugins import cartesia

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's. Its `import protocol` sees the demo's own copy only while it loads.
_demo = Path(__file__).resolve().parents[1] / "demos" / "postop-checkin"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _demo / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo():
    protocol = _load("playground_postop_protocol", "protocol.py")
    previous = sys.modules.get("protocol")
    sys.modules["protocol"] = protocol
    try:
        agent = _load("playground_postop_agent", "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("protocol", None)
        else:
            sys.modules["protocol"] = previous
    return protocol, agent


protocol, _module = _load_demo()
CheckInCall = _module.CheckInCall
make_stt = _module.make_stt
publish_checkin = _module.publish_checkin

HOSTED_INSTRUCTIONS = (
    " This is a public demo with a two-minute limit, so keep every reply to one short "
    "sentence plus the next question."
)


def instructions() -> str:
    return _module.INSTRUCTIONS + HOSTED_INSTRUCTIONS


def greeting(state: dict) -> str:
    return (
        f"Say this is a simulated check-in from {protocol.HOSPITAL}, a made-up hospital, "
        "so they should play the patient with made-up answers and it is not medical "
        f"advice. Say you are calling {state['patient']['name']} for day 3 after their "
        f"knee replacement, then ask: {protocol.STEPS[0]['ask']}"
    )


def make_tts() -> cartesia.TTS:
    # Sonic 2 retires on 2026-10-20.
    return cartesia.TTS(model="sonic-3")


def initial_state() -> dict:
    # A fresh patient and empty protocol per call: never shared between calls.
    return protocol.initial_state()
