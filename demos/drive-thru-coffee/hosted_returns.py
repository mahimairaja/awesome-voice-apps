"""Maintainer-owned adapter for the standalone returns desk QA demo."""

import importlib.util
from decimal import Decimal
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's `agent` module. It already runs on Deepgram, OpenAI and Cartesia.
_demo = Path(__file__).resolve().parents[1] / "returns-desk-qa"
_spec = importlib.util.spec_from_file_location("playground_returns_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
ReturnsDesk = _module.ReturnsDesk
initial_state = _module.initial_state

# The QA grader calls OpenAI directly, outside the AgentSession that
# VoiceGateway meters, so the hosted agent bills each grade itself.
# gpt-4.1-mini list price: $0.40 per 1M input tokens, $1.60 per 1M output tokens.
GRADE_INPUT_USD = Decimal("0.40") / 1_000_000
GRADE_OUTPUT_USD = Decimal("1.60") / 1_000_000
# Grades allowed in a full two-minute call: one per agent turn, with headroom.
GRADE_BUDGET = 14

GREETING = (
    "In two short sentences: say this is a returns desk simulation for Fernway Goods "
    "and this call is monitored for quality, with the grades on screen. Then ask for "
    "the order number and ZIP code, which are on their screen."
)


def grade_cost(prompt_tokens: int, completion_tokens: int) -> Decimal:
    return max(0, prompt_tokens) * GRADE_INPUT_USD + max(0, completion_tokens) * GRADE_OUTPUT_USD


def publish_returns(room, state: dict, qa) -> None:
    _module.publish_orders(room, state)
    qa.publish()
