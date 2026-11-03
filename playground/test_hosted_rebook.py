"""Offline tests for the flight rebooking policy and outage paths; never call providers."""

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from livekit.agents import llm

import hosted_rebook

module = hosted_rebook._module


@asynccontextmanager
async def filler(*_args, **_kwargs):
    yield


class RebookPolicy(unittest.IsolatedAsyncioTestCase):
    async def notes(self, agent, text):
        """Run one finished caller turn; returns the system notes added for the model."""
        turn = llm.ChatContext.empty()
        await agent.on_user_turn_completed(turn, llm.ChatMessage(role="user", content=[text]))
        return " ".join(item.text_content for item in turn.messages() if item.role == "system")

    async def test_bag_and_seat_answers_need_check_policy_before_rebook(self):
        agent = module.FlightRebooker(SimpleNamespace())
        context = SimpleNamespace(update=AsyncMock(), with_filler=filler)
        with (
            patch.object(module, "publish_ui_event"),
            patch.object(module, "SEARCH_SECONDS", 0),
        ):
            await agent.search_flights(context)
            self.assertIn("check_policy for bags", await self.notes(agent, "Will my bag move?"))
            self.assertIn("check_policy for seat", await self.notes(agent, "A window seat?"))
            blocked = await agent.rebook(context, "A")
            self.assertTrue(blocked.startswith("blocked"))
            self.assertIn("bags and seat", blocked)
            self.assertIsNone(agent.state["rebooked"])
            await agent.check_policy(context, "bags")
            self.assertIn("seat", await agent.rebook(context, "A"))
            self.assertEqual(await self.notes(agent, "And my bag again?"), "")
            await agent.check_policy(context, "seat")
            self.assertIn("Borealis 418", await agent.rebook(context, "A"))
        self.assertEqual(agent.state["booking"]["seat"], "15A window")

    async def test_a_caller_who_never_asks_rebooks_without_extra_lookups(self):
        agent = module.FlightRebooker(SimpleNamespace())
        context = SimpleNamespace(update=AsyncMock(), with_filler=filler)
        with (
            patch.object(module, "publish_ui_event"),
            patch.object(module, "SEARCH_SECONDS", 0),
        ):
            self.assertEqual(await self.notes(agent, "I need Vancouver tonight."), "")
            await agent.search_flights(context)
            self.assertIn("Borealis 102", await agent.rebook(context, "C"))

    async def test_the_outage_request_reaches_simulate_outage_after_rebooking(self):
        agent = module.FlightRebooker(SimpleNamespace())
        context = SimpleNamespace(update=AsyncMock(), with_filler=filler)
        with (
            patch.object(module, "publish_ui_event"),
            patch.object(module, "SEARCH_SECONDS", 0),
        ):
            await agent.search_flights(context)
            await agent.rebook(context, "A")
            self.assertIn("simulate_outage", await self.notes(agent, "Now knock out the voice."))
            self.assertIn("now failing", await agent.simulate_outage(context, "voice"))
        self.assertTrue(agent.outage.down("tts"))


if __name__ == "__main__":
    unittest.main()
