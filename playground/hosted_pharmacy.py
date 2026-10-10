"""Maintainer-owned adapter for the standalone pharmacy refill demo."""

import importlib.util
import sys
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's. Its `import refill` sees the demo's own copy only while it loads.
_demo = Path(__file__).resolve().parents[1] / "demos" / "pharmacy-refill"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _demo / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo():
    refill = _load("playground_pharmacy_refill", "refill.py")
    previous = sys.modules.get("refill")
    sys.modules["refill"] = refill
    try:
        agent = _load("playground_pharmacy_agent", "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("refill", None)
        else:
            sys.modules["refill"] = previous
    return refill, agent


refill, _module = _load_demo()
RefillLine = _module.RefillLine
make_stt = _module.make_stt
publish_refill = _module.publish_refill

GREETING = (
    f"Say this is the {refill.PHARMACY} refill line simulation: the prescription on "
    "screen is made up and nothing is filled. Ask which medication they want to refill."
)
HOSTED_INSTRUCTIONS = (
    " This is a public demo with a two-minute limit, so keep every reply to one or "
    "two short sentences."
)


def instructions() -> str:
    return _module.INSTRUCTIONS + HOSTED_INSTRUCTIONS


def initial_state() -> dict:
    # A fresh prescription per call, dated from today: never shared between calls.
    return refill.initial_state()
