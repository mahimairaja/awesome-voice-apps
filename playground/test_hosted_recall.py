"""Offline tests for the returning caller demo; never call provider APIs."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hosted_recall
from hosted_recall import memory

RESERVATION = "598cd768-86d4-42a1-bb44-adc44fba4207"


class FakeSite:
    """The site's recall endpoint, kept in memory with the demo's own policy."""

    def __init__(self) -> None:
        self.entries: list[dict] = []
        self.ops: list[str] = []

    async def __call__(self, action: str, reservation: str, op: str, **fields) -> dict:
        self.ops.append(op)
        try:
            if op == "save":
                self.entries = memory.add(
                    self.entries, fields["kind"], fields["text"], fields["why"], time.time()
                )
            elif op == "forget_all":
                self.entries = memory.forget_all(self.entries, time.time(), "caller")
        except ValueError as problem:
            return {"refused": str(problem)}
        return {"entries": self.entries}


def build(site):
    room = SimpleNamespace(local_participant=MagicMock(publish_data=AsyncMock()))
    store = hosted_recall.SiteStore(site, RESERVATION)
    return hosted_recall.Concierge(room, store), SimpleNamespace(
        userdata=hosted_recall.initial_state()
    )


class RecallDemo(unittest.IsolatedAsyncioTestCase):
    async def test_consent_alone_keeps_nothing_and_says_to_call_remember(self):
        site = FakeSite()
        agent, context = build(site)
        consented = await agent.record_consent(context, True)
        self.assertIn("nothing is kept yet", consented)
        self.assertIn("Call remember", consented)
        self.assertEqual(memory.notes(site.entries, time.time()), [])

        kept = await agent.remember(
            context, "context", "Flying to Lisbon on the 20th.", "So the card is not flagged."
        )
        self.assertTrue(kept.startswith("kept as m"))
        self.assertEqual(site.ops, ["save", "save"])
        notes = memory.notes(site.entries, time.time())
        self.assertEqual([n["text"] for n in notes], ["Flying to Lisbon on the 20th."])

    async def test_a_site_refusal_is_reported_as_nothing_kept(self):
        async def refusing(action, reservation, op, **fields):
            return {"refused": "no account for this reservation"}

        agent, context = build(refusing)
        result = await agent.record_consent(context, True)
        self.assertIn("not recorded: no account for this reservation", result)
        self.assertIn("nothing was kept", result)


if __name__ == "__main__":
    unittest.main()
