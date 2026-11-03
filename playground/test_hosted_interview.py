"""Offline tests for the hosted interview handoff; never call provider APIs."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from livekit.agents import NOT_GIVEN

import hosted_interview


class Session:
    def __init__(self):
        self.handlers = {}
        self.update_agent = MagicMock()

    def on(self, name, handler):
        self.handlers[name] = handler

    def emit(self, name, **fields):
        self.handlers[name](SimpleNamespace(**fields))


def message(role, text="", interrupted=False):
    return SimpleNamespace(role=role, text_content=text, metrics={}, interrupted=interrupted)


def desk():
    session = Session()
    room = SimpleNamespace(local_participant=SimpleNamespace(publish_data=AsyncMock()))
    built = []

    def build(architecture, label, question):
        built.append(label)
        return SimpleNamespace(architecture=architecture, label=label)

    order = ("cascade", "duplex")
    return hosted_interview.InterviewDesk(session, room, order, build=build), session, built


class InterviewHandoff(unittest.IsolatedAsyncioTestCase):
    async def test_lobby_hands_off_again_once_the_session_has_started(self):
        # The lobby's update_agent ran inside session.start() and only recorded A.
        interview, session, _ = desk()
        first = interview.first_agent()
        session.emit("agent_state_changed", old_state="initializing", new_state="listening")
        session.update_agent.assert_called_once_with(first)
        session.emit("agent_state_changed", old_state="listening", new_state="speaking")
        session.update_agent.assert_called_once()
        interview.close()

    async def test_a_handoff_that_already_took_is_not_repeated(self):
        interview, session, _ = desk()
        interview.first_agent()
        session.emit("agent_state_changed", old_state="listening", new_state="speaking")
        session.emit("agent_state_changed", old_state="initializing", new_state="listening")
        session.update_agent.assert_not_called()
        interview.close()

    async def test_a_reply_cut_by_the_candidate_does_not_end_the_round(self):
        interview, session, built = desk()
        interview.first_agent()
        session.emit("conversation_item_added", item=message("assistant", "Hi, I'm A."))
        # One spoken answer, split at a pause: the reply in the pause gets talked over.
        session.emit("conversation_item_added", item=message("user", "I rebuilt billing."))
        session.emit("conversation_item_added", item=message("assistant", "That", True))
        session.emit("conversation_item_added", item=message("user", "It halved failures."))
        session.emit("conversation_item_added", item=message("assistant", "What was hard?"))
        session.update_agent.assert_not_called()
        self.assertEqual(interview.answers, 1)
        session.emit("conversation_item_added", item=message("user", "Replaying events."))
        session.emit("conversation_item_added", item=message("assistant", "Thank you."))
        session.update_agent.assert_called_once()
        self.assertEqual(built, ["A", "B"])
        interview.close()

    async def test_hosted_cascade_keeps_the_session_turn_detector(self):
        build = hosted_interview.bounded_builder(AsyncMock(), MagicMock())
        cascade = build("cascade", "A", "Why?")
        self.assertIs(cascade.turn_detection, NOT_GIVEN)
        await cascade.llm.aclose()


if __name__ == "__main__":
    unittest.main()
