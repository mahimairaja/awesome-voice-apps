"""Offline tests for the hosted payer verification call; never call provider APIs."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import hosted
import hosted_payer

module = hosted_payer._module
ROOM = "playground-598cd768-86d4-42a1-bb44-adc44fba4207"


def build():
    room = SimpleNamespace(
        name=ROOM,
        local_participant=SimpleNamespace(publish_dtmf=AsyncMock(), publish_data=AsyncMock()),
    )
    with patch.dict(hosted.os.environ, {"CARTESIA_API_KEY": "offline"}):
        agent = hosted_payer.PayerCaller(room)
    handle = MagicMock()
    handle.wait_for_playout = AsyncMock()
    handle.done.return_value = False
    session = SimpleNamespace(
        userdata=hosted_payer.initial_state(), say=MagicMock(return_value=handle)
    )
    agent._activity = SimpleNamespace(session=session)
    return agent, session


def said(session) -> list[str]:
    return [c.args[0] for c in session.say.call_args_list if c.args]


class PayerRep(unittest.IsolatedAsyncioTestCase):
    async def test_a_rep_who_picks_up_mid_menu_hears_the_ai_disclosure(self):
        # Evidence, 2026-11-02 eval: the rep spoke during the menu and the agent never
        # said it was an AI assistant for the clinic.
        agent, session = build()
        self.assertNotIn("Open with one sentence", agent.instructions)
        with (
            patch.object(type(agent), "session", property(lambda self: self._activity.session)),
            patch.object(module, "publish"),
        ):
            session.userdata["stage"] = "ivr"
            agent._call = asyncio.ensure_future(asyncio.sleep(60))
            rep = SimpleNamespace(
                text_content="Harborline Health provider services, this is Dana. How can I help?"
            )
            await agent.on_user_turn_completed(None, rep)
            await asyncio.sleep(0)
            self.assertTrue(agent._call.cancelled())
            self.assertEqual(session.userdata["stage"], "human")
            self.assertEqual(said(session)[-1], module.OPENING)
            self.assertIn("an AI assistant calling for Lakeview Family Medicine", module.OPENING)
            # A menu recording is still never answered.
            agent, session = build()
            session.userdata["stage"] = "ivr"
            menu = SimpleNamespace(text_content="For members, press two.")
            with self.assertRaises(module.StopResponse):
                await agent.on_user_turn_completed(None, menu)
            self.assertEqual(said(session), [])

    async def test_record_benefits_refuses_a_copay_the_rep_did_not_say(self):
        # Evidence, 2026-11-02 eval: "The specialist copy is dollar fifty." became 1.5.
        agent, session = build()
        session.userdata["stage"] = "human"
        with (
            patch.object(type(agent), "session", property(lambda self: self._activity.session)),
            patch.object(module, "publish"),
        ):
            heard = SimpleNamespace(text_content="The specialist copy is dollar fifty.")
            await agent.on_user_turn_completed(None, heard)
            context = SimpleNamespace(userdata=session.userdata)
            note = await agent.record_benefits(context, specialist_copay_usd=1.5)
            self.assertIn("ask the rep to repeat", note)
            self.assertEqual(session.userdata["record"]["copay"]["status"], "empty")
            note = await agent.record_benefits(context, specialist_copay_usd=50)
        self.assertIn("Specialist copay $50", note)


if __name__ == "__main__":
    unittest.main()
