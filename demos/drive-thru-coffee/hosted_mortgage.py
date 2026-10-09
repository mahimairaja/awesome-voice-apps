"""Maintainer-owned adapter for the standalone mortgage renewal demo."""

import importlib.util
from pathlib import Path

# Load by path so the demo's `agent` module never shadows the coffee demo's.
_demo = Path(__file__).resolve().parents[1] / "mortgage-renewal"
_spec = importlib.util.spec_from_file_location("playground_mortgage_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
MortgageAdvisor = _module.MortgageAdvisor
GREETING = _module.GREETING
TURN_HANDLING = _module.TURN_HANDLING
