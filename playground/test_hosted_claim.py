"""Offline tests for the claim intake demo; never call provider APIs."""

import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import hosted_claim

agent = hosted_claim._module


class ClaimSpokenAnswers(unittest.IsolatedAsyncioTestCase):
    def test_spelled_policy_numbers_normalise(self):
        for heard in (
            "AB123456",
            "ab-123-456",
            "A B one two three four five six",
            "a b 1 2 3 4 5 6",
        ):
            with self.subTest(heard=heard):
                self.assertEqual(agent.validate_policy_number(heard), (True, "AB123456"))

    def test_a_short_policy_number_is_rejected_with_the_real_rule(self):
        ok, reason = agent.validate_policy_number("A B one two three")
        self.assertFalse(ok)
        self.assertIn("heard AB123", reason)
        self.assertIn("two letters then six to eight digits", reason)

    def test_dates_of_loss_accept_relative_and_spoken_forms(self):
        today = agent.date(2026, 11, 2)
        for heard, expected in (
            ("yesterday", "2026-11-01"),
            ("It happened yesterday.", "2026-11-01"),
            ("today", "2026-11-02"),
            ("the day before yesterday", "2026-10-31"),
            ("October 28th, 2026", "2026-10-28"),
            ("28 October 2026", "2026-10-28"),
            ("10/28/2026", "2026-10-28"),
            ("2026-10-28", "2026-10-28"),
            # No year: the most recent one, never the future.
            ("December 30", "2025-12-30"),
        ):
            with self.subTest(heard=heard):
                self.assertEqual(agent.validate_date_of_loss(heard, today), (True, expected))
        self.assertFalse(agent.validate_date_of_loss("tomorrow", today)[0])
        self.assertFalse(agent.validate_date_of_loss("2026-11-03", today)[0])

    def test_yes_or_no_answers_read_the_callers_words(self):
        cases = {
            "No. Nobody was hurt.": "no",
            "no one was injured": "no",
            "It isn't": "no",
            "Yeah, my passenger": "yes",
            "yes": "yes",
        }
        for said, expected in cases.items():
            self.assertEqual(agent.normalize_yesno(said), (True, expected), said)
        for unsure in (
            "Not sure",
            "I don't know",
            "Someone was hurt",
            "I can't tell if anyone was hurt",
            "No, wait, yes, my passenger was hurt",
        ):
            self.assertFalse(agent.normalize_yesno(unsure)[0], unsure)

    async def test_hosted_record_field_takes_the_callers_words(self):
        intake = hosted_claim.ClaimIntake(SimpleNamespace())
        context = SimpleNamespace(userdata=hosted_claim.initial_state())
        with patch.object(agent, "publish_ui_event"):
            policy = await intake.record_field(
                context, "policy_number", "A B one two three four five six"
            )
            loss = await intake.record_field(context, "date_of_loss", "yesterday")
        self.assertEqual((policy, loss), ("recorded policy_number", "recorded date_of_loss"))
        claim = context.userdata["claim"]
        self.assertEqual(claim["policy_number"], "AB123456")
        yesterday = hosted_claim.today() - timedelta(days=1)
        self.assertEqual(claim["date_of_loss"], yesterday.isoformat())
        self.assertIn("never reject a value yourself", hosted_claim.instructions())


if __name__ == "__main__":
    unittest.main()
