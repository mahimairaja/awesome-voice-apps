"""Maintainer-owned adapter for the standalone agent stress test demo."""

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's. Its `import stresstest` sees the demo's own copy only while it loads.
_demo = Path(__file__).resolve().parents[1] / "demos" / "agent-stress-test"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _demo / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo():
    stresstest = _load("playground_stresstest", "stresstest.py")
    previous = sys.modules.get("stresstest")
    sys.modules["stresstest"] = stresstest
    try:
        agent = _load("playground_stresstest_agent", "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("stresstest", None)
        else:
            sys.modules["stresstest"] = previous
    return stresstest, agent


stresstest, _module = _load_demo()
StressTestLead = _module.StressTestLead

# gpt-4o-mini list prices: the simulated callers, Ava and the judges all run on it.
SIM_USD_PER_PROMPT_TOKEN = Decimal("0.15") / 1_000_000
SIM_USD_PER_CACHED_TOKEN = Decimal("0.075") / 1_000_000
SIM_USD_PER_COMPLETION_TOKEN = Decimal("0.60") / 1_000_000
# A full v1 suite plus v2 costs about one cent; refuse new work past this.
SIM_USD_CAP = Decimal("0.06")

GREETING = (
    "Say this is a stress test simulation: Ava, the billing agent of Halden Fiber, a "
    "made-up internet provider, is on screen. Ask whether to throw all six simulated "
    "callers at her or just a few, and mention they can also call her themselves."
)


def sim_cost(metrics) -> Decimal:
    cached = max(0, metrics.prompt_cached_tokens)
    fresh = max(0, metrics.prompt_tokens - cached)
    return (
        fresh * SIM_USD_PER_PROMPT_TOKEN
        + cached * SIM_USD_PER_CACHED_TOKEN
        + max(0, metrics.completion_tokens) * SIM_USD_PER_COMPLETION_TOKEN
    )


def initial_state() -> dict:
    # Fresh runs and scorecards per call: never shared between visitors.
    return stresstest.initial_state()
