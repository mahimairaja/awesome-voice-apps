"""Maintainer-owned adapter for the standalone manager approval demo.

The returns agent can warm-transfer to a manager agent the hosted worker did
not construct. The manager gets the call's per-call provider clients and
charges its LLM and TTS work to the same HostedGuard limits as the returns
agent, so a transfer can never reset a cap.
"""

import importlib.util
from pathlib import Path

from livekit.plugins import cartesia

_demo = Path(__file__).resolve().parents[1] / "manager-approval"
_spec = importlib.util.spec_from_file_location("playground_approval_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

RefundDesk = _module.RefundDesk
StoreManager = _module.StoreManager
GREETING = (
    "Say you are Sky from Harbor & Pine returns, a made-up store: this is a simulation, "
    "nothing is refunded, and their order is on screen. Ask how you can help."
)


def initial_state() -> dict:
    return _module.new_case()


def publish_approval(room, case: dict) -> None:
    _module.publish_case(room, case)


def voice(role: str) -> cartesia.TTS:
    """A fresh per-call client; sonic-3, since sonic-2 is being retired."""
    return cartesia.TTS(
        model="sonic-3",
        voice=_module.MANAGER_VOICE if role == "manager" else _module.AGENT_VOICE,
    )


class HostedManager(StoreManager):
    """The manager after a warm transfer, on the returns agent's call budget."""

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
