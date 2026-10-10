"""Maintainer-owned adapter for the standalone delivery window call demo."""

import importlib.util
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's `agent` module.
_demo = Path(__file__).resolve().parents[1] / "demos" / "delivery-window-call"
_spec = importlib.util.spec_from_file_location("playground_delivery_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
DeliveryCaller = _module.DeliveryCaller
initial_state = _module.initial_state
publish_delivery = _module.publish
