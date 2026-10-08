"""Maintainer-owned adapter for the standalone water tracker demo."""

import importlib.util
from pathlib import Path

# Keep the contributed demo self-contained; load it by path so its `agent`
# module never shadows the coffee demo's `agent` module.
_demo = Path(__file__).resolve().parents[1] / "water-tracker"
_spec = importlib.util.spec_from_file_location("playground_water_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
WaterCoach = _module.WaterCoach
DEFAULT_GOAL = _module.DEFAULT_GOAL


def initial_state() -> dict:
    return {"glasses": 0, "goal": DEFAULT_GOAL}


def publish_water(room, data: dict) -> None:
    _module._publish_stat(room, data, data["glasses"], data["goal"])
