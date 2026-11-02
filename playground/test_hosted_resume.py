"""Offline tests for the hosted loan callback; never call provider APIs."""

import unittest
from types import SimpleNamespace

import hosted_resume

application = hosted_resume.application
FIRST_SIX = [
    ("owner_name", "John Doe"),
    ("business_name", "Doe Bakery Limited"),
    ("years_in_business", "6"),
    ("annual_revenue", "800000"),
    ("loan_amount", "200000"),
    ("use_of_funds", "A second oven and a delivery van"),
]


def callback():
    agent = hosted_resume.LoanCallback(SimpleNamespace())
    agent.publish = lambda: None
    return agent


async def six_answers(agent):
    for field, value in FIRST_SIX:
        await agent.record_answer(field, value)


class ResumeDemo(unittest.IsolatedAsyncioTestCase):
    async def test_an_unanswered_debt_is_never_recorded_as_zero(self):
        agent = callback()
        await six_answers(agent)
        result = await agent.record_answer("monthly_debt", "0")
        self.assertTrue(result.startswith("rejected"), result)
        self.assertIn(application.PROMPTS["monthly_debt"], result)
        self.assertNotIn("monthly_debt", agent.state["answers"])
        self.assertTrue(application.submit(agent.state).startswith("rejected"))
        # The caller really has no debt: their words make zero an answer.
        result = await agent.record_answer("monthly_debt", "0", "", "We have no debt.")
        self.assertTrue(result.startswith("recorded monthly debt: $0"), result)

    async def test_placeholder_text_is_not_an_answer(self):
        agent = callback()
        result = await agent.record_answer("use_of_funds", "N/A")
        self.assertTrue(result.startswith("rejected"), result)
        self.assertNotIn("use_of_funds", agent.state["answers"])

    async def test_a_correction_overwrites_and_the_read_back_and_submit_use_it(self):
        agent = callback()
        await six_answers(agent)
        result = await agent.record_answer("monthly_debt", "4000", "", "About four thousand")
        self.assertIn("Read back exactly $200,000 for A second oven and a delivery van", result)
        result = await agent.record_answer(
            "loan_amount", "150000", "", "make the amount one hundred fifty thousand"
        )
        self.assertTrue(result.startswith("updated amount: $150,000, replacing $200,000"))
        self.assertIn("Read back exactly $150,000 for A second oven and a delivery van", result)
        self.assertIn("$150,000", application.context_message(agent.state))
        submitted = await agent.submit_application()
        self.assertTrue(submitted.startswith("submitted. Reference NB-"), submitted)
        self.assertIn("$150,000 for A second oven", submitted)

    async def test_the_same_value_again_is_unchanged_and_points_at_the_next_question(self):
        agent = callback()
        await six_answers(agent)
        result = await agent.record_answer("loan_amount", "200000")
        self.assertTrue(result.startswith("unchanged"), result)
        self.assertIn(application.PROMPTS["monthly_debt"], result)


if __name__ == "__main__":
    unittest.main()
