"""Maintainer-owned adapter for the standalone clinic scheduler demo."""

import importlib.util
from pathlib import Path

# Keep the contributed demo self-contained; load it by path so its `agent`
# module never shadows the coffee demo's `agent` module.
_demo = Path(__file__).resolve().parents[1] / "clinic-scheduler"
_spec = importlib.util.spec_from_file_location("playground_clinic_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
ClinicScheduler = _module.ClinicScheduler


def initial_state() -> dict:
    # Slots are rebuilt from today's date for every call, like the standalone demo.
    return {"available_slots": _module._build_slots(), "booking": None, "ui_mounted": set()}


def publish_clinic(room, data: dict) -> None:
    _module._publish_slots(room, data["ui_mounted"], data["available_slots"])
