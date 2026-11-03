"""Offline tests for the panel scribe demo; never call provider APIs."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hosted_panel
from livekit.agents import ToolError
from livekit.agents.llm import ChatMessage

# The 2026-11-02 eval call: three seats, then the recap request.
LINES = (
    "Hiring manager here. I liked her ownership of the payments migration, "
    "but I'm not sure she has mentored anyone yet. I lean hire.",
    "Engineer here. Her system design was clean and she handled the scaling "
    "question well, but she skipped testing entirely. I'd say hire.",
    "Recruiter here. She has another offer and wants above our band, so we need "
    "to move this week. Scribe, recap.",
)


def row(interviewer, strengths, concerns, lean="hire"):
    return hosted_panel.ScorecardRow(
        interviewer=interviewer, strengths=strengths, concerns=concerns, lean=lean
    )


class PanelScorecard(unittest.IsolatedAsyncioTestCase):
    async def debrief(self):
        scribe = hosted_panel.SoloPanelScribe(SimpleNamespace())
        for line in LINES:
            try:
                await scribe.on_user_turn_completed(None, ChatMessage(role="user", content=[line]))
            except hosted_panel.StopResponse:
                pass
        return scribe

    async def test_silent_lines_stay_in_the_recap_context(self):
        with patch.object(hosted_panel._module, "publish_ui_event"):
            scribe = await self.debrief()
        said = [item.text_content for item in scribe.chat_ctx.items if item.type == "message"]
        self.assertTrue(said[0].startswith("[Hiring manager] I liked her ownership"))
        self.assertTrue(said[1].startswith("[Engineer] Her system design"))
        # The recap line itself reaches the model through the reply, not this context.
        self.assertEqual(len(said), 2)

    async def test_missing_seats_and_invented_notes_are_refused(self):
        with patch.object(hosted_panel._module, "publish_ui_event") as publish:
            scribe = await self.debrief()
            publish.reset_mock()
            # The eval call's only row, with strengths the recruiter never said.
            with self.assertRaises(ToolError) as refused:
                await scribe.publish_scorecard(
                    None,
                    [
                        row(
                            "Recruiter",
                            "Strong experience and good cultural fit.",
                            "Salary expectations above our band and competing offer.",
                        )
                    ],
                    "Lean hire.",
                )
        message = str(refused.exception)
        self.assertIn("add a row for Hiring manager", message)
        self.assertIn("add a row for Engineer", message)
        self.assertIn("Recruiter strengths", message)
        self.assertNotIn("Recruiter concerns", message)
        publish.assert_not_called()

    async def test_a_grounded_row_per_seat_is_published_in_panel_order(self):
        with patch.object(hosted_panel._module, "publish_ui_event") as publish:
            scribe = await self.debrief()
            publish.reset_mock()
            result = await scribe.publish_scorecard(
                None,
                [
                    row("Recruiter", "None stated.", "Competing offer, wants above band."),
                    row("Engineer", "Clean system design.", "Skipped testing."),
                    row("Hiring manager", "Strong ownership.", "Has not mentored anyone."),
                ],
                "The panel leans hire if mentoring and testing are covered.",
            )
        self.assertEqual(result, "scorecard published")
        table = publish.call_args_list[0].kwargs["props"]
        self.assertEqual([r[0] for r in table["rows"]], ["Hiring manager", "Engineer", "Recruiter"])
        self.assertEqual(table["rows"][0][1:3], ["Strong ownership.", "Has not mentored anyone."])

    async def test_rows_for_seats_that_never_spoke_are_dropped(self):
        scribe = hosted_panel.SoloPanelScribe(SimpleNamespace())
        with patch.object(hosted_panel._module, "publish_ui_event") as publish:
            for line in ("Engineer here. Her system design was solid.", "Scribe, recap."):
                try:
                    await scribe.on_user_turn_completed(
                        None, ChatMessage(role="user", content=[line])
                    )
                except hosted_panel.StopResponse:
                    pass
            publish.reset_mock()
            await scribe.publish_scorecard(
                None,
                [row("Engineer", "design", "none"), row("Recruiter", "-", "-", "undecided")],
                "Lean hire.",
            )
        table = publish.call_args_list[0].kwargs["props"]
        self.assertEqual(table["rows"], [["Engineer", "design", "none", "hire"]])


if __name__ == "__main__":
    unittest.main()
