"""Maintainer-owned adapter for the standalone payer verification demo."""

import importlib.util
import sys
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's. Its `import payer` sees the demo's own copy only while it loads.
_demo = Path(__file__).resolve().parents[1] / "payer-verification"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _demo / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo():
    payer = _load("playground_payer_line", "payer.py")
    previous = sys.modules.get("payer")
    sys.modules["payer"] = payer
    try:
        agent = _load("playground_payer_agent", "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("payer", None)
        else:
            sys.modules["payer"] = previous
    return payer, agent


payer, _module = _load_demo()
PayerCaller = _module.PayerCaller
publish_payer = _module.publish
AGENT_VOICE = _module.AGENT_VOICE

HOSTED_INSTRUCTIONS = (
    " This is a public demo with a two-minute limit, so keep every reply to one short "
    "sentence and ask for two items at a time."
)


def instructions() -> str:
    return _module.INSTRUCTIONS + HOSTED_INSTRUCTIONS


def initial_state() -> dict:
    # A fresh menu order and rep screen per call: never shared between calls.
    return _module.initial_state()
