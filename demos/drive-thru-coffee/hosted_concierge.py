"""Maintainer-owned adapter for the standalone hotel concierge demo."""

import importlib.util
import os
from decimal import Decimal
from pathlib import Path

# Load by path so the contributed `agent` module never shadows the coffee one.
_demo = Path(__file__).resolve().parents[1] / "hotel-concierge"
_spec = importlib.util.spec_from_file_location("playground_concierge_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
HotelConcierge = _module.HotelConcierge
SyncMeter = _module.SyncMeter
initial_state = _module.initial_state
CONCIERGE = _module.CONCIERGE
HOTEL = _module.HOTEL

# Anam bills per second of avatar session. Set ANAM_USD_PER_MINUTE to the rate
# on the account; the default is deliberately on the high side.
DEFAULT_USD_PER_MINUTE = Decimal("0.20")


def _rate() -> Decimal:
    try:
        rate = Decimal(os.environ.get("ANAM_USD_PER_MINUTE", DEFAULT_USD_PER_MINUTE))
    except ArithmeticError:
        return DEFAULT_USD_PER_MINUTE
    # A bad value must never under-bill or stop the other demos from loading.
    return rate if rate.is_finite() and rate > 0 else DEFAULT_USD_PER_MINUTE


AVATAR_USD_PER_MINUTE = _rate()


def avatar_cost(seconds: float) -> Decimal:
    """Cost of an avatar session, billed by the started second."""
    return Decimal(max(0, int(-(-seconds // 1)))) * AVATAR_USD_PER_MINUTE / 60


def publish_concierge(room, data: dict) -> None:
    _module.publish_picks(room, data, data["restaurants"])
