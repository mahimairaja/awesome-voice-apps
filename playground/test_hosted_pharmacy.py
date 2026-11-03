"""Offline tests for the pharmacy refill demo; never call provider APIs."""

import unittest
from datetime import date

import hosted_pharmacy

refill = hosted_pharmacy.refill
TODAY = date(2026, 11, 2)


class PharmacyDateOfBirth(unittest.TestCase):
    def test_spoken_and_written_dates_normalise_to_iso(self):
        for heard in (
            "June 28, 1981",
            "june 28th 1981",
            "28 June 1981",
            "the 28th of June, 1981",
            "Jun. 28, 1981",
            "1981-06-28",
            "06/28/1981",
            "28/06/1981",
        ):
            with self.subTest(heard=heard):
                self.assertEqual(refill.normalize_dob(heard, TODAY), (True, "1981-06-28"))

    def test_impossible_or_partial_dates_are_rejected_without_a_format_demand(self):
        for heard in ("June 1981", "sometime in the eighties", "2030-01-01", "02/30/1981"):
            with self.subTest(heard=heard):
                ok, reason = refill.normalize_dob(heard, TODAY)
                self.assertFalse(ok)
                self.assertNotIn("YYYY", reason)

    def test_capture_reads_back_a_spoken_birth_date(self):
        state = refill.initial_state({**refill.new_profile(), "date_of_birth": "1981-06-28"})
        result = refill.capture(state, "date_of_birth", "June 28, 1981", TODAY)
        self.assertIn('"1981-06-28"', result)
        self.assertEqual(refill.confirm(state, "date_of_birth"), "confirmed date of birth")
        self.assertIn("June 28th, 1981", refill.speakable(result)[0])

    def test_prompt_never_asks_the_caller_for_a_date_format(self):
        prompt = hosted_pharmacy.instructions()
        self.assertNotIn("YYYY-MM-DD", prompt)
        self.assertIn("never ask the caller for a format", prompt)


if __name__ == "__main__":
    unittest.main()
