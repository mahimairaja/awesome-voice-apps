"""Maintainer-owned adapter for the standalone medical bill explainer demo."""

import importlib.util
from decimal import Decimal
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's `agent` module.
_demo = Path(__file__).resolve().parents[1] / "medical-bill-explainer"
_spec = importlib.util.spec_from_file_location("playground_bill_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
BillExplainer = _module.BillExplainer
audit = _module.audit
normalize = _module.normalize

# Published gpt-4.1-mini prices: $0.40 per 1M input tokens, $1.60 per 1M output.
# VoiceGateway never sees the direct vision request, so each bill read is billed here.
VISION_IN_USD = Decimal("0.40") / 1_000_000
VISION_OUT_USD = Decimal("1.60") / 1_000_000


def vision_cost(usage) -> Decimal:
    if usage is None:
        # Unknown usage is billed as a full high-detail page, never as free.
        return Decimal(4000) * VISION_IN_USD + Decimal(1200) * VISION_OUT_USD
    return (
        Decimal(max(0, usage.prompt_tokens)) * VISION_IN_USD
        + Decimal(max(0, usage.completion_tokens)) * VISION_OUT_USD
    )


GREETING = (
    "Say this is a member services simulation for Kestrel Health, a made-up health plan, "
    "and nothing is saved. Ask them to pick a sample bill on screen, or upload a made-up "
    "one, and say which line confuses them."
)
