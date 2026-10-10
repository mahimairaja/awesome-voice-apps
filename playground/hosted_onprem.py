"""Maintainer-owned adapter for the standalone private health line demo.

The demo already uses the playground's providers, so the hosted copy keeps its
stack and only adds the playground's time limit to the instructions.
"""

import importlib.util
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the coffee demo's.
_demo = Path(__file__).resolve().parents[1] / "demos" / "private-health-line"
_spec = importlib.util.spec_from_file_location("playground_onprem_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
PrivateHealthLine = _module.PrivateHealthLine
initial_state = _module.initial_state

INSTRUCTIONS = _module.INSTRUCTIONS + (
    " This is a public demo with a two-minute limit and made-up details: if one "
    "answer covers several questions, record each before asking the next."
)
GREETING = (
    f"Say you are the {_module.HOSPITAL} clinical trial check-in line and that this "
    "is a simulation, so they should use a made-up participant number. Ask for it."
)
