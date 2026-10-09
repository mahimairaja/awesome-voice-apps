"""Maintainer-owned adapter for the standalone private health line demo.

The demo already talks only to a self-hosted model server, so the hosted copy
keeps its stack and swaps nothing. It only adds the playground's time limit to
the instructions and bills the call as GPU time instead of metered tokens.
"""

import importlib.util
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the coffee demo's.
_demo = Path(__file__).resolve().parents[1] / "private-health-line"
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
    f"Say you are the {_module.HOSPITAL} clinical trial check-in line, that this is "
    "a simulation with made-up details, and that the call stays on the hospital's "
    "own servers. Ask for their participant number or name."
)


def gpu_usd_per_second() -> Decimal:
    """The model server's hourly price, spread over the seconds a call holds it."""
    try:
        hourly = Decimal(os.environ.get("ONPREM_GPU_USD_PER_HOUR", ""))
    except InvalidOperation:
        hourly = Decimal(-1)
    if not hourly.is_finite() or not 0 < hourly <= 20:
        raise RuntimeError("Set ONPREM_GPU_USD_PER_HOUR to the model server's hourly price")
    return hourly / 3600
