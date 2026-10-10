"""Maintainer-owned adapter for the standalone loan callback demo."""

import importlib.util
import sys
from pathlib import Path

# Load the contributed demo by path so its `agent` module never shadows the
# coffee demo's. Its `import application` sees the demo's own copy only while it loads.
_demo = Path(__file__).resolve().parents[1] / "demos" / "loan-callback"


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, _demo / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo():
    application = _load("playground_resume_application", "application.py")
    previous = sys.modules.get("application")
    sys.modules["application"] = application
    try:
        agent = _load("playground_resume_agent", "agent.py")
    finally:
        if previous is None:
            sys.modules.pop("application", None)
        else:
            sys.modules["application"] = previous
    return application, agent


application, _module = _load_demo()
LoanCallback = _module.LoanCallback

HOSTED_INSTRUCTIONS = (
    " This is a public demo and each call lasts about a minute, so keep every reply "
    "to one short sentence and ask the next question straight away."
)


def instructions() -> str:
    return _module.INSTRUCTIONS + HOSTED_INSTRUCTIONS


class SiteStore:
    """Checkpoints in the site's D1 database, keyed by the signed-in account.

    The worker never sees the account: it sends the reservation id and the site
    looks up whose call it is, so one visitor can never load another's application.
    """

    name = "Cloudflare D1"

    def __init__(self, control, reservation: str) -> None:
        self.control = control
        self.reservation = reservation

    async def load(self) -> dict | None:
        result = await self.control("resume", self.reservation)
        return result.get("state")

    async def save(self, state: dict, view: dict) -> None:
        await self.control(
            "checkpoint", self.reservation, version=state["version"], state=state, view=view
        )
