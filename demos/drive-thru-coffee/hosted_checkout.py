"""Maintainer-owned adapter for the standalone voice checkout demo."""

import importlib.util
from pathlib import Path

# Keep the contributed demo self-contained; load it by path so its `agent`
# module never shadows the coffee demo's `agent` module.
_demo = Path(__file__).resolve().parents[1] / "voice-checkout"
_spec = importlib.util.spec_from_file_location("playground_checkout_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
VoiceCheckout = _module.VoiceCheckout
FIELDS = _module.FIELDS

GREETING = (
    "Say this is a checkout simulation for a made-up outdoor store: nothing is charged "
    "or shipped, so use made-up details. Say you fill the page on their screen as they "
    "talk, and ask what they would like to buy."
)
