"""Offline tests for the hosted delivery window call; never dial or call providers."""

import asyncio
import datetime
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import hosted
import hosted_delivery

ID = "598cd768-86d4-42a1-bb44-adc44fba4207"
ROOM = f"playground-{ID}"
MONDAY = datetime.date(2026, 10, 5)


class Speech:
    def __init__(self):
        self.wait_for_playout = AsyncMock()


class Probe(hosted_delivery.DeliveryCaller):
    """A caller with a fake session and room, so the call logic runs offline."""

    session = None

    def __init__(self, phone=None):
        self.room = SimpleNamespace(
            name=ROOM, local_participant=SimpleNamespace(publish_data=AsyncMock())
        )
        self.phone = phone
        self.callee = "visitor-1"
        self._hung_up = False
        self._call = None
        self.said = []
        self.session = SimpleNamespace(
            userdata=hosted_delivery.initial_state(phone, MONDAY),
            interrupt=MagicMock(),
            say=self._say,
            generate_reply=MagicMock(),
        )
        self.hang_up = AsyncMock()

    def _say(self, text, **_kwargs):
        self.said.append(text)
        return Speech()


def dtmf(digit, identity="visitor-1"):
    return SimpleNamespace(digit=digit, code=0, participant=SimpleNamespace(identity=identity))


def amd(category, transcript="Hello?"):
    return SimpleNamespace(
        category=SimpleNamespace(value=category),
        reason="llm",
        transcript=transcript,
        speech_duration=0.6,
        delay=0.9,
    )


class DeliveryCall(unittest.IsolatedAsyncioTestCase):
    def test_only_a_signed_north_american_number_is_dialled(self):
        self.assertEqual(hosted_delivery.phone_from('{"phone": "+14165550123"}'), "+14165550123")
        for metadata in (
            "",
            "not json",
            '{"phone": "+441632960961"}',
            '{"phone": "+11165550123"}',
            '{"phone": 14165550123}',
            '{"agent": "delivery"}',
        ):
            self.assertIsNone(hosted_delivery.phone_from(metadata))

    def test_windows_fall_on_weekdays_and_state_is_per_call(self):
        first = hosted_delivery.initial_state(None, datetime.date(2026, 10, 9))  # a Friday
        second = hosted_delivery.initial_state(None, datetime.date(2026, 10, 9))
        self.assertEqual(first["windows"][0]["label"], "Tuesday October 13, 1 to 5 PM")
        self.assertEqual(first["windows"][1]["label"], "Monday October 12, 8 AM to noon")
        self.assertIsNot(first["keys"], second["keys"])
        self.assertEqual(first["mode"], "browser")
        phone = hosted_delivery.initial_state("+14165550123")
        self.assertEqual(phone["mode"], "phone")
        # The page never receives the number being dialled.
        self.assertNotIn("+14165550123", json.dumps(hosted_delivery._module.snapshot(phone)))

    async def test_press_one_confirms_without_the_llm(self):
        agent = Probe()
        agent.session.userdata.update(stage="human", menu="main", started=0)
        agent._on_dtmf(dtmf("1"))
        await asyncio.sleep(0)
        data = agent.session.userdata
        self.assertEqual(data["outcome"], "confirmed")
        self.assertEqual(data["keys"][0]["meaning"], "confirm")
        agent.session.interrupt.assert_called_once()
        agent.session.generate_reply.assert_not_called()
        self.assertIn("Confirmed for Wednesday October 7", agent.said[0])
        agent.hang_up.assert_awaited_once()

    async def test_press_two_then_a_window_reschedules(self):
        agent = Probe()
        agent.session.userdata.update(stage="human", menu="main", started=0)
        await agent.press("2")
        self.assertEqual(agent.session.userdata["menu"], "reschedule")
        self.assertIn("Press 1 for Tuesday October 6, 8 AM to noon", agent.said[0])
        await agent.press("7")
        self.assertIsNone(agent.session.userdata["outcome"])
        await agent.press("3")
        data = agent.session.userdata
        self.assertEqual((data["outcome"], data["order"]["window"]), ("rescheduled", "w4"))
        self.assertEqual([k["digit"] for k in data["keys"]], ["2", "7", "3"])
        agent.hang_up.assert_awaited_once()

    async def test_keys_from_others_or_before_answer_are_ignored(self):
        agent = Probe()
        agent.session.userdata.update(stage="ringing", menu="main", started=0)
        await agent.press("1")
        agent.session.userdata["stage"] = "human"
        agent._on_dtmf(dtmf("1", identity="someone-else"))
        agent._on_dtmf(dtmf("A"))
        await asyncio.sleep(0)
        self.assertEqual(agent.session.userdata["keys"], [])
        self.assertIsNone(agent.session.userdata["outcome"])

    async def test_spoken_answers_use_the_same_actions(self):
        agent = Probe()
        context = SimpleNamespace(userdata=agent.session.userdata)
        reply = await agent.reschedule_window(context, "w9")
        self.assertIn("w2:", reply)
        await agent.reschedule_window(context, "w3")
        self.assertEqual(context.userdata["order"]["window"], "w3")
        self.assertIn("Already settled", await agent.confirm_window(context))

    async def test_voicemail_gets_a_message_then_a_hang_up(self):
        agent = Probe()
        agent.session.userdata["started"] = 0
        await agent._on_amd(amd("machine-vm", "Hi, you've reached Sam, leave a message."))
        data = agent.session.userdata
        self.assertEqual(data["outcome"], "voicemail-left")
        self.assertEqual(data["amd"]["category"], "machine-vm")
        self.assertIn("N B - 4 8 2 1 3", agent.said[0])
        self.assertEqual([t["stage"] for t in data["timeline"]], ["voicemail", "ended"])
        agent.hang_up.assert_awaited_once()

    async def test_a_person_opens_the_keypad_menu(self):
        agent = Probe()
        agent.session.userdata["started"] = 0
        await agent._on_amd(amd("human"))
        self.assertEqual(agent.session.userdata["menu"], "main")
        agent.session.generate_reply.assert_not_called()
        await agent._on_amd(amd("uncertain", ""))
        agent.session.generate_reply.assert_called_once()

    async def test_browser_no_answer_ends_the_call(self):
        agent = Probe()
        agent.room.remote_participants = {}
        agent.room.on = MagicMock()
        agent.room.off = MagicMock()
        with patch.object(hosted_delivery._module, "RING_SECONDS", 0.01):
            self.assertFalse(await agent._wait_for_browser_answer())
        self.assertEqual(agent.session.userdata["outcome"], "no-answer")

    async def test_hosted_call_listens_first_and_hangs_up_through_the_site(self):
        self.assertEqual(hosted.HostedDelivery.greeting, "")
        agent = object.__new__(hosted.HostedDelivery)
        agent._hung_up = False
        agent._finish = AsyncMock()
        await agent.hang_up()
        await agent.hang_up()
        agent._finish.assert_awaited_once()

    async def test_hosted_agent_reads_the_number_from_the_signed_dispatch(self):
        hosted.claims[ROOM] = {"id": ID}
        ctx = SimpleNamespace(
            room=SimpleNamespace(name=ROOM),
            job=SimpleNamespace(
                metadata=json.dumps({"agent": "delivery", "phone": "+14165550123"})
            ),
        )
        env = {"DEEPGRAM_API_KEY": "x", "OPENAI_API_KEY": "x", "CARTESIA_API_KEY": "x"}
        with (
            patch.dict(hosted.os.environ, env),
            patch.object(hosted, "get_job_context", return_value=ctx),
        ):
            agent = hosted.HostedDelivery()
        self.assertEqual(agent.phone, "+14165550123")
        self.assertEqual(agent.initial_state()["mode"], "phone")
        await agent.llm.aclose()


if __name__ == "__main__":
    unittest.main()
