"""Offline tests for the hosted support cost router; never call provider APIs."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hosted
import hosted_cost

ID = "598cd768-86d4-42a1-bb44-adc44fba4207"
ROOM = f"playground-{ID}"
KEYS = {"DEEPGRAM_API_KEY": "offline", "OPENAI_API_KEY": "offline", "CARTESIA_API_KEY": "offline"}


def build():
    hosted.claims[ROOM] = {"id": ID, "seconds": 120}
    with (
        patch.dict(hosted.os.environ, KEYS),
        patch.object(
            hosted, "get_job_context", return_value=SimpleNamespace(room=SimpleNamespace(name=ROOM))
        ),
    ):
        return hosted.HostedCost()


class CostPlanTerms(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        hosted.claims.clear()

    async def test_plan_questions_are_answered_from_the_plan_terms(self):
        agent = build()
        # The live prompt sends plan questions to the tool, never to common practice.
        self.assertIn("look_up_plan", agent.instructions)
        self.assertNotIn("common carrier practice", agent.instructions)
        self.assertIn("look_up_plan", {tool.id for tool in agent.tools})
        terms = await agent.look_up_plan(SimpleNamespace())
        self.assertIn(hosted_cost.router.faq_answer("roaming"), terms)
        self.assertIn("not in the plan terms", terms)
        await agent.llm.aclose()
        await agent.on_exit()


if __name__ == "__main__":
    unittest.main()
