"""Maintainer-owned adapter for the standalone build-your-own-agent demo."""

import importlib.util
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's `agent` module.
_demo = Path(__file__).resolve().parents[1] / "build-your-own-agent"
_spec = importlib.util.spec_from_file_location("playground_builder_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
ConfigurableAgent = _module.ConfigurableAgent
DEFAULT_CONFIG = _module.DEFAULT_CONFIG
VOICES = _module.VOICES
parse_config = _module.parse_config

GREETING = (
    "Greet the caller as the business, in one short sentence. Mention once that this "
    "is a demo agent the visitor just configured, then ask how you can help."
)
