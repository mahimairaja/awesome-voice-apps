"""Offline tests for the trivia demo; never call provider APIs."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import trivia


class TriviaRepeat(unittest.IsolatedAsyncioTestCase):
    async def turn_line(self, agent, state):
        turn = MagicMock()
        with patch.object(type(agent), "session", SimpleNamespace(userdata=state)):
            await agent.on_user_turn_completed(turn, None)
        return turn.add_message.call_args.kwargs["content"]

    async def test_every_turn_names_the_live_question_from_state(self):
        agent = trivia.HostedTriviaHost(SimpleNamespace())
        state = trivia.initial_state()
        line = await self.turn_line(agent, state)
        self.assertIn("question 1 of 3: Which planet is closest to the Sun?", line)
        with patch("trivia.publish_trivia"):
            await agent.answer_question(SimpleNamespace(userdata=state), 1, "Mercury")
        # "Can you say that one again?" after a tool turn gets question two verbatim.
        line = await self.turn_line(agent, state)
        self.assertIn("question 2 of 3: How many sides does a hexagon have?", line)
        self.assertIn("word for word", line)
        self.assertNotIn("Six", line)
        self.assertIn("latest system line", agent.instructions)

    async def test_the_line_gives_the_score_once_the_round_is_over(self):
        agent = trivia.HostedTriviaHost(SimpleNamespace())
        state = trivia.initial_state()
        with patch("trivia.publish_trivia"):
            for number, answer in enumerate(("Mercury", "six", "skip"), start=1):
                await agent.answer_question(SimpleNamespace(userdata=state), number, answer)
        line = await self.turn_line(agent, state)
        self.assertEqual(line, "The round is over. Final score: 2 of 3.")


if __name__ == "__main__":
    unittest.main()
