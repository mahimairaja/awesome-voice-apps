"""Maintainer-owned adapter for the standalone flight rebooking demo."""

import importlib.util
from pathlib import Path

# Keep the contributed demo self-contained; load it by path so its `agent`
# module never shadows the coffee demo's `agent` module.
_demo = Path(__file__).resolve().parents[1] / "flight-rebooking"
_spec = importlib.util.spec_from_file_location("playground_rebook_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
FlightRebooker = _module.FlightRebooker
build_llm = _module.build_llm
build_tts = _module.build_tts
