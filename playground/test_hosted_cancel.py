"""Offline tests for the hosted fair cancellation tools; never call providers."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from livekit.agents import Agent

import hosted_cancel

policy = hosted_cancel.policy


def line(state):
    agent = hosted_cancel.CancelLine(SimpleNamespace())
    return agent, MagicMock(userdata=state)


async def offered_tools(agent, session):
    """The tool names the model is shown on its next request."""
    seen = []

    async def fake_llm_node(self, chat_ctx, tools, model_settings):
        seen.extend(tool.id for tool in tools)
        yield "ok"

    with (
        patch.object(type(agent), "session", property(lambda self: session)),
        patch.object(Agent, "llm_node", fake_llm_node),
    ):
        async for _ in agent.llm_node(None, agent.tools, None):
            pass
    return seen


class CancelTools(unittest.IsolatedAsyncioTestCase):
    async def test_make_offer_is_hidden_when_switching_gets_no_offer(self):
        state = hosted_cancel.initial_state()
        agent, session = line(state)
        self.assertNotIn("make_offer", await offered_tools(agent, session))
        policy.observe_caller(state, "I want to cancel my Lumora Plus membership.")
        policy.note_reason(state, "switching")
        tools = await offered_tools(agent, session)
        self.assertNotIn("make_offer", tools)
        self.assertIn("cancel_membership", tools)
        # A call that slips through is still blocked and offers nothing.
        with patch.object(hosted_cancel._module, "publish_ui_event"):
            result = await agent.make_offer(SimpleNamespace(userdata=state))
        self.assertTrue(result.startswith("blocked: no offer fits"))
        self.assertEqual(state["offers"], [])

    async def test_make_offer_is_shown_once_for_price(self):
        state = hosted_cancel.initial_state()
        agent, session = line(state)
        policy.observe_caller(state, "I'd like to cancel.")
        policy.note_reason(state, "price")
        self.assertIn("make_offer", await offered_tools(agent, session))
        policy.make_offer(state)
        self.assertNotIn("make_offer", await offered_tools(agent, session))

    async def test_cancel_membership_waits_for_a_decline(self):
        state = hosted_cancel.initial_state()
        agent, _ = line(state)
        context = SimpleNamespace(userdata=state)
        policy.observe_caller(state, "I'd like to cancel my membership please.")
        policy.note_reason(state, "price")
        policy.make_offer(state)
        policy.observe_caller(state, "Could you just give me a free month instead?")
        with patch.object(hosted_cancel._module, "publish_ui_event"):
            blocked = await agent.cancel_membership(context)
            self.assertTrue(blocked.startswith("blocked: the offer is still open"))
            self.assertIsNone(state["outcome"])
            # Without an open offer the model may still cancel on request.
            fresh = hosted_cancel.initial_state()
            policy.observe_caller(fresh, "cancel please")
            policy.note_reason(fresh, "switching")
            done = await agent.cancel_membership(SimpleNamespace(userdata=fresh))
        self.assertTrue(done.startswith("cancelled"))
        self.assertIn("only offer", hosted_cancel.instructions())


if __name__ == "__main__":
    unittest.main()
