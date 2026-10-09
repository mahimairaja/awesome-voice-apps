"""Maintainer-owned adapter for the standalone returning caller demo.

The contributed demo keeps its client file in a local JSON file. Hosted, the
file lives on the site (D1), keyed by the signed-in visitor's account, which
the worker never sees: every read and write names the reservation, and the site
resolves the account from it. The memory policy (kinds, expiry, never-kept
patterns) is the demo's own memory.py, checked again server-side.
"""

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

_demo = Path(__file__).resolve().parents[1] / "returning-caller"

# gpt-4o-mini list prices, for the post-call summary VoiceGateway does not see.
SUMMARY_USD_PER_INPUT_TOKEN = Decimal("0.15") / 1_000_000
SUMMARY_USD_PER_OUTPUT_TOKEN = Decimal("0.60") / 1_000_000


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _demo / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo():
    memory = _load("playground_recall_memory", "memory.py")
    previous = sys.modules.get("memory")
    sys.modules["memory"] = memory
    try:
        agent = _load("playground_recall_agent", "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("memory", None)
        else:
            sys.modules["memory"] = previous
    return memory, agent


memory, _module = _load_demo()
Concierge = _module.Concierge
greeting = _module.greeting
initial_state = _module.initial_state
publish_recall = _module.publish_recall

HOSTED_INSTRUCTIONS = (
    " This is a public demo with a two-minute limit, so keep every reply to one or "
    "two short sentences. The caller can see the client file on screen."
)


def summary_cost(prompt_tokens: int, completion_tokens: int) -> Decimal:
    return (
        Decimal(max(0, prompt_tokens)) * SUMMARY_USD_PER_INPUT_TOKEN
        + Decimal(max(0, completion_tokens)) * SUMMARY_USD_PER_OUTPUT_TOKEN
    )


class SiteStore:
    """The client file, kept by the site. Same interface as the demo's FileStore."""

    def __init__(self, control, reservation: str) -> None:
        self.control = control
        self.reservation = reservation

    async def _call(self, op: str, **fields) -> list[dict]:
        result = await self.control("recall", self.reservation, op=op, **fields)
        if "refused" in result:
            raise ValueError(result["refused"])
        return result["entries"]

    async def load(self) -> list[dict]:
        return await self._call("load")

    async def save(self, kind: str, text: str, why: str) -> list[dict]:
        return await self._call("save", kind=kind, text=text, why=why)

    async def forget(self, memory_id: str, by: str) -> list[dict]:
        return await self._call("forget", memory=memory_id)

    async def forget_all(self, by: str) -> list[dict]:
        return await self._call("forget_all")
