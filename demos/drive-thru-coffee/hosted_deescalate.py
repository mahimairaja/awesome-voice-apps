"""Maintainer-owned adapter for the standalone billing de-escalation demo.

The demo retunes its Cartesia voice every turn, so each call needs its own
sonic-3 client: `update_options` on a shared client would change the voice
of every concurrent call.
"""

import importlib.util
from pathlib import Path

from livekit.plugins import cartesia

# Load by path so the demo's `agent` module never shadows the coffee demo's.
_demo = Path(__file__).resolve().parents[1] / "billing-deescalation"
_spec = importlib.util.spec_from_file_location("playground_deescalate_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
BillingDesk = _module.BillingDesk
GREETING = _module.GREETING


def voice() -> cartesia.TTS:
    """A fresh per-call client, starting in the voice of a tense caller."""
    return cartesia.TTS(model="sonic-3", voice=_module.VOICE, **_module.VOICE_BY_BAND["tense"])
