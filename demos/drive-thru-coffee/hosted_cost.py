"""Maintainer-owned adapter for the standalone support cost router demo."""

import importlib.util
import sys
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's. Its `import router` sees the demo's own copy only while it loads.
_demo = Path(__file__).resolve().parents[1] / "support-cost-router"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _demo / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo():
    router = _load("playground_cost_router", "router.py")
    previous = sys.modules.get("router")
    sys.modules["router"] = router
    try:
        agent = _load("playground_cost_agent", "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("router", None)
        else:
            sys.modules["router"] = previous
    return router, agent


router, _module = _load_demo()
CostRouter = _module.CostRouter

# HostedGuard.on_enter asks for a greeting; CostRouter answers it with its
# scripted line instead of a model call, which the panel shows as turn one.
GREETING = "Greet the caller."
