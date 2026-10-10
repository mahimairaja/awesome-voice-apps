"""Maintainer-owned adapter for the standalone phone tree router demo."""

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's. Its `import routing` sees the demo's own copy only while it loads.
_demo = Path(__file__).resolve().parents[1] / "demos" / "phone-tree-router"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _demo / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo():
    routing = _load("playground_ivr_routing", "routing.py")
    previous = sys.modules.get("routing")
    sys.modules["routing"] = routing
    try:
        agent = _load("playground_ivr_agent", "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("routing", None)
        else:
            sys.modules["routing"] = previous
    return routing, agent


routing, _module = _load_demo()
PhoneTreeRouter = _module.PhoneTreeRouter
publish_router = _module.publish_router

# gpt-4o-mini list price, per token: the router call is billed like any LLM turn.
ROUTER_INPUT_USD = Decimal("0.15") / 1_000_000
ROUTER_OUTPUT_USD = Decimal("0.60") / 1_000_000

GREETING = (
    f"Say {routing.BRAND} support, that this is a demo line, and ask in a few words what "
    "they are calling about. One short sentence."
)
HOSTED_INSTRUCTIONS = (
    " This is a public demo with a two-minute limit: nothing is real, no account is "
    "looked up and no one is actually transferred."
)


def instructions() -> str:
    return _module.INSTRUCTIONS + HOSTED_INSTRUCTIONS


def initial_state() -> dict:
    return routing.initial_state()


def router_cost(prompt_tokens: int, completion_tokens: int) -> Decimal:
    return (
        Decimal(max(0, prompt_tokens)) * ROUTER_INPUT_USD
        + Decimal(max(0, completion_tokens)) * ROUTER_OUTPUT_USD
    )
