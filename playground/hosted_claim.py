"""Maintainer-owned adapter for the standalone claim intake demo."""

import importlib.util
import sys
import types
from datetime import UTC, date, datetime
from pathlib import Path

# The contributed demo imports its own AssemblyAI, Gemini and Inworld plugins at
# module level. The hosted worker swaps to the metered Deepgram, OpenAI and
# Cartesia stack, so give those imports inert placeholders for the load only
# instead of shipping three unused provider SDKs in the image.
_PROVIDERS = ("assemblyai", "google", "inworld")


def _load():
    demo = Path(__file__).resolve().parents[1] / "demos" / "claim-intake"
    spec = importlib.util.spec_from_file_location("playground_claim_agent", demo / "agent.py")
    module = importlib.util.module_from_spec(spec)
    added = []
    for name in _PROVIDERS:
        key = f"livekit.plugins.{name}"
        if key not in sys.modules:
            sys.modules[key] = types.ModuleType(key)
            added.append(key)
    try:
        spec.loader.exec_module(module)
    finally:
        for key in added:
            sys.modules.pop(key, None)
    return module


_module = _load()
ClaimIntake = _module.ClaimIntake


def today() -> date:
    return datetime.now(UTC).date()


# The demo pins TODAY at import. A worker runs for days, so judge the date of
# loss and stamp the claim number against the current date instead.
_module.VALIDATORS["date_of_loss"] = lambda value: _module.validate_date_of_loss(value, today())
_module._make_claim_ref = lambda: f"CLM-{today():%Y%m%d}-{_module.uuid.uuid4().hex[:4].upper()}"

HOSTED_INSTRUCTIONS = (
    " Today is {today} (UTC). This is a public demo with a two-minute limit: if the "
    "caller gives several details in one answer, record each of them before "
    "asking for the next missing field."
)


def instructions() -> str:
    return _module.INSTRUCTIONS + HOSTED_INSTRUCTIONS.format(today=f"{today():%A, %B %d, %Y}")


def initial_state() -> dict:
    return {"claim": {}, "claim_ref": None}


def publish_claim(room, data: dict) -> None:
    _module._publish_claim(room, data["claim"])
