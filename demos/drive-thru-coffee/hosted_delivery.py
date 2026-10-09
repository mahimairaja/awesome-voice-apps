"""Maintainer-owned adapter for the standalone delivery window call demo."""

import importlib.util
import json
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's `agent` module.
_demo = Path(__file__).resolve().parents[1] / "delivery-window-call"
_spec = importlib.util.spec_from_file_location("playground_delivery_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
DeliveryCaller = _module.DeliveryCaller
initial_state = _module.initial_state
publish_delivery = _module.publish
PHONE = _module.PHONE


def phone_from(metadata: str) -> str | None:
    """The number to dial, if the site admitted a phone call for this visitor.

    The site signs the dispatch metadata inside the visitor's token, after it has
    verified the number and checked the daily limits. Anything malformed means a
    browser call, never a dial.
    """
    try:
        phone = json.loads(metadata or "{}").get("phone")
    except (TypeError, ValueError):
        return None
    return phone if isinstance(phone, str) and PHONE.match(phone) else None
