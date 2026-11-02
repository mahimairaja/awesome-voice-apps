"""Offline tests for the hosted post-op check-in; never call provider APIs."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hosted_postop

protocol = hosted_postop.protocol


def call():
    room = SimpleNamespace(local_participant=MagicMock(publish_data=AsyncMock()))
    state = hosted_postop.initial_state()
    return hosted_postop.CheckInCall(room), SimpleNamespace(userdata=state), state


class PostopDemo(unittest.IsolatedAsyncioTestCase):
    async def test_a_reading_never_needs_or_records_chills(self):
        agent, context, state = call()
        result = await agent.record_temperature(context, 101.8, "F", None, "one oh one point eight")
        self.assertTrue(result.startswith("recorded temperature: 101.8°F"), result)
        self.assertNotIn("chills", result)
        self.assertEqual(state["answers"]["temperature"]["rules"], ["N2"])

    async def test_no_reading_asks_about_chills_before_recording(self):
        agent, context, state = call()
        result = await agent.record_temperature(context, None, "unknown", None, "no thermometer")
        self.assertTrue(result.startswith("rejected"), result)
        self.assertIn(protocol.PARTS["temperature"]["chills_or_sweats"], result)
        self.assertIsNone(state["answers"]["temperature"])
        await agent.record_temperature(context, None, "unknown", True, "some sweats")
        self.assertEqual(state["answers"]["temperature"]["rules"], ["N3"])

    async def test_an_unanswered_wound_part_is_asked_one_at_a_time(self):
        agent, context, state = call()
        words = "It's not red and there's a little bit of clear fluid on the bandage."
        result = await agent.record_wound(context, False, "clear", None, words)
        self.assertTrue(result.startswith("rejected"), result)
        self.assertIn(protocol.PARTS["wound"]["edges_opening"], result)
        self.assertNotIn(protocol.PARTS["wound"]["drainage"], result)
        self.assertIsNone(state["answers"]["wound"])
        await agent.record_wound(context, False, "clear", False, "the edges are still closed")
        self.assertEqual(state["answers"]["wound"]["summary"], "Clear drainage")

    async def test_pain_needs_both_parts(self):
        agent, context, state = call()
        result = await agent.record_pain(context, 6, None, "about a six")
        self.assertIn(protocol.PARTS["pain"]["medication_helping"], result)
        self.assertIsNone(state["answers"]["pain"])
        result = await agent.record_pain(context, None, True, "the pills help")
        self.assertIn(protocol.PARTS["pain"]["score"], result)
        await agent.record_pain(context, 6, True, "about a six, and the pills help")
        self.assertEqual(state["answers"]["pain"]["summary"], "6/10, medication helping")

    def test_the_hosted_prompt_keeps_one_question_at_a_time(self):
        self.assertIn("Ask one question at a time", hosted_postop.instructions())
        self.assertIn("pass null", hosted_postop.instructions())


if __name__ == "__main__":
    unittest.main()
