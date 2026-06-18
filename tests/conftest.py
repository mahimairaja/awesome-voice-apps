"""Shared fixtures for the demo eval suite.

Each demo's eval runs its Agent in text mode (no audio) against the demo's real
LLM, then scores the conversation with livekit.agents.evals judges. The judge is
a consistent openai gpt-4o-mini. See
docs/superpowers/specs/2026-06-18-eval-suite-design.md.
"""

import pytest
import pytest_asyncio


class _FakeLocalParticipant:
    async def publish_data(self, *args, **kwargs):
        return None


class FakeRoom:
    """Minimal stand-in for an rtc.Room. The demos publish UI on tool calls via
    publish_ui_event (room.local_participant.publish_data) and track mounted
    components by setattr on the room; a text eval has no real room, so this
    makes those calls harmless no-ops."""

    def __init__(self) -> None:
        self.name = "eval-room"
        self.local_participant = _FakeLocalParticipant()


@pytest.fixture
def fake_room() -> FakeRoom:
    return FakeRoom()


@pytest_asyncio.fixture
async def judge_llm():
    from livekit.plugins import openai

    llm = openai.LLM(model="gpt-4o-mini")
    yield llm
    aclose = getattr(llm, "aclose", None)
    if aclose is not None:
        await aclose()
