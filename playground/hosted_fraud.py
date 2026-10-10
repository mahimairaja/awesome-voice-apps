"""Maintainer-owned adapter for the standalone card fraud line demo.

The fraud line hands the call to agents the hosted worker did not construct:
a verification AgentTask and a fraud specialist. Each gets the call's
per-call provider clients and charges its LLM and TTS work to the same
HostedGuard limits as the front desk, so a handoff can never reset a cap.
"""

import importlib.util
from pathlib import Path

from livekit.plugins import cartesia

_demo = Path(__file__).resolve().parents[1] / "demos" / "card-fraud-line"
_spec = importlib.util.spec_from_file_location("playground_fraud_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

FrontDesk = _module.FrontDesk
VerifyCaller = _module.VerifyCaller
FraudDesk = _module.FraudDesk
GREETING = (
    "Say this is a simulated fraud alert from Larkfield Bank, a made-up bank: nothing "
    "real is charged or frozen, and the cardholder's details are on screen. " + _module.GREETING
)


def initial_state() -> dict:
    return _module.new_case()


def publish_fraud(room, case: dict) -> None:
    _module.publish_case(room, case)


def watch(session, room) -> None:
    _module.watch(session, room)


def voice(role: str) -> cartesia.TTS:
    """A fresh per-call client; sonic-3, since sonic-2 is being retired."""
    return cartesia.TTS(
        model="sonic-3",
        voice=_module.SPECIALIST_VOICE if role == "specialist" else _module.FRONT_DESK_VOICE,
    )


class CallBudget:
    """Charge an agent's LLM and TTS work to the call's guard.

    `guard` is the HostedGuard front desk; it owns the counters, the deadline
    and the shutdown, which all outlive a handoff.
    """

    def __init__(self, guard, *args, **kwargs) -> None:
        self._guard = guard
        super().__init__(*args, **kwargs)

    async def llm_node(self, chat_ctx, tools, model_settings):
        if not self._guard.charge_llm(chat_ctx):
            return
        async for chunk in super().llm_node(chat_ctx, tools, model_settings):
            yield chunk

    async def tts_node(self, text, model_settings):
        async for frame in super().tts_node(self._guard.bound_text(text), model_settings):
            yield frame


class HostedVerify(CallBudget, VerifyCaller):
    pass


class HostedFraudDesk(CallBudget, FraudDesk):
    pass
