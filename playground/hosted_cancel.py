"""Maintainer-owned adapter for the standalone fair cancellation demo."""

import importlib.util
import sys
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's. Its `import policy` sees the demo's own copy only while it loads.
_demo = Path(__file__).resolve().parents[1] / "demos" / "fair-cancellation"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _demo / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo():
    policy = _load("playground_cancel_policy", "policy.py")
    previous = sys.modules.get("policy")
    sys.modules["policy"] = policy
    try:
        agent = _load("playground_cancel_agent", "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("policy", None)
        else:
            sys.modules["policy"] = previous
    return policy, agent


policy, _module = _load_demo()
CancelLine = _module.CancelLine
publish_cancel = _module.publish_cancel

GREETING = (
    f"Say this is the {policy.SPOKEN_BRAND} membership line simulation: the account on "
    "screen is made up and nothing is charged. Ask how you can help today."
)
HOSTED_INSTRUCTIONS = (
    " This is a public demo with a two-minute limit, so keep every reply to one short "
    "sentence where you can."
)


def instructions() -> str:
    return _module.INSTRUCTIONS + HOSTED_INSTRUCTIONS


def initial_state() -> dict:
    # A fresh made-up account per call: never shared between calls.
    return policy.initial_state()
