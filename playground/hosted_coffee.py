"""Maintainer-owned adapter for the standalone drive-thru coffee demo."""

import importlib.util
import sys
from pathlib import Path

# The coffee demo stays a plain LiveKit agent in its own folder. Register it as
# `agent`, the name the worker has always imported it under.
_demo = Path(__file__).resolve().parents[1] / "demos" / "drive-thru-coffee"
_spec = importlib.util.spec_from_file_location("agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
sys.modules["agent"] = _module
_spec.loader.exec_module(_module)
DriveThruAttendant = _module.DriveThruAttendant
publish_ui_event = _module.publish_ui_event
_publish_cart = _module._publish_cart
_publish_menu = _module._publish_menu
