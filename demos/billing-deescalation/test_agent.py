"""Offline tests for the frustration meter, the credit limit and the handoff.

No provider is called. Run: uv run python -m unittest test_agent
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import agent


class FakeParticipant:
    def __init__(self) -> None:
        self.sent = []

    async def publish_data(self, payload, topic, reliable):
        self.sent.append(payload)


def make_desk() -> agent.BillingDesk:
    room = SimpleNamespace(local_participant=FakeParticipant())
    return agent.BillingDesk(room)


class MeterTest(unittest.TestCase):
    def test_anger_and_timing_raise_the_score(self):
        meter = agent.FrustrationMeter()
        record = meter.score_turn("This is ridiculous, a total rip off", 1.5, barged_in=True)
        kinds = [s["kind"] for s in record["signals"]]
        self.assertEqual(kinds, ["anger", "anger", "barge_in", "fast"])
        # 12 + 12 + 10 + 8 - 5 is capped at the per-turn rise.
        self.assertEqual(record["score"], agent.START + agent.MAX_RISE)
        self.assertEqual(record["strategy"], "label")
        self.assertEqual(record["band"], "heated")
        self.assertEqual(record["voice"], agent.VOICE_BY_BAND["heated"])

    def test_most_specific_move_wins(self):
        meter = agent.FrustrationMeter()
        self.assertEqual(
            meter.score_turn("I already told you that", 2, False)["strategy"], "reflect"
        )
        self.assertEqual(meter.score_turn("I want a manager", 2, False)["strategy"], "choice")

    def test_being_heard_and_credits_cool_the_caller(self):
        meter = agent.FrustrationMeter()
        meter.relief()
        record = meter.score_turn("okay, thanks", 1, False)
        self.assertLess(record["score"], agent.START)
        self.assertEqual(record["strategy"], "confirm")
        # Relief is used once.
        self.assertNotIn(
            "progress", [s["kind"] for s in meter.score_turn("hm", 1, False)["signals"]]
        )

    def test_substrings_do_not_match(self):
        self.assertEqual(agent.find_cues("my agentic hello shell"), {})

    def test_handoff_on_threshold(self):
        meter = agent.FrustrationMeter()
        first = meter.score_turn("this is a scam, I'm cancelling, damn it", 4, True)
        self.assertEqual(first["band"], "heated")
        record = meter.score_turn("ridiculous garbage, I'm switching to another carrier", 4, True)
        self.assertGreaterEqual(record["score"], agent.HANDOFF_AT)
        self.assertEqual(record["strategy"], "handoff")
        self.assertTrue(meter.handoff_reason.startswith("Frustration reached"))

    def test_handoff_when_asked_twice(self):
        meter = agent.FrustrationMeter(score=10)
        meter.score_turn("can I get a supervisor", 2, False)
        record = meter.score_turn("a real person please", 2, False)
        self.assertEqual(record["strategy"], "handoff")
        self.assertEqual(meter.handoff_reason, "Asked for a person twice")

    def test_handoff_when_anger_does_not_cool(self):
        meter = agent.FrustrationMeter(score=74)
        for text in ("I am angry", "no", "no"):
            record = meter.score_turn(text, 2, False)
        self.assertEqual(meter.handoff_reason, "Above 70 for 3 turns")
        self.assertEqual(record["band"], "handoff")

    def test_history_is_bounded(self):
        meter = agent.FrustrationMeter()
        for _ in range(20):
            meter.score_turn("hello " * 80, 10, False)
        props = meter.props()
        self.assertEqual(len(props["turns"]), agent.KEEP)
        self.assertEqual(len(props["points"]), agent.KEEP + 1)
        self.assertLessEqual(len(props["turns"][0]["said"]), 80)


class DeskTest(unittest.IsolatedAsyncioTestCase):
    async def test_credit_limit_is_enforced_in_code(self):
        desk = make_desk()
        self.assertIn("Credited $17.00", await desk.apply_credit(None, "protection"))
        self.assertIn("Credited $25.00", await desk.apply_credit(None, "late_fee"))
        self.assertIn("Over your limit", await desk.apply_credit(None, "roaming"))
        self.assertIn("cannot be credited", await desk.apply_credit(None, "plan"))
        self.assertIn("already credited", await desk.apply_credit(None, "late_fee"))
        props = agent.bill_props(desk.bill)
        self.assertEqual(props["credited"], 42.0)
        self.assertEqual(props["due"], 173.0)
        self.assertEqual(props["authority_left"], 33.0)
        self.assertEqual(desk.meter.pending_relief, 2)

    async def test_handoff_packet_and_tools_close(self):
        desk = make_desk()
        await desk.apply_credit(None, "protection")
        with patch.object(agent, "publish_ui_event") as publish:
            await desk.transfer_to_specialist(None)
        component, action, props = publish.call_args.args[1:]
        self.assertEqual((component, action), ("Handoff", "mount"))
        self.assertEqual(props["open"], ["US roaming, 9 days", "Late payment fee"])
        self.assertEqual(props["credited"], 17.0)
        self.assertIn("specialist", await desk.apply_credit(None, "roaming"))


if __name__ == "__main__":
    unittest.main()
