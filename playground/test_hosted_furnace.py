"""Offline tests for the furnace repair demo; never call provider APIs."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hosted_furnace

agent = hosted_furnace._module


def line():
    # Skip __init__: it builds the turn detector, which these tool tests do not need.
    furnace = object.__new__(hosted_furnace.FurnaceLine)
    furnace.room = SimpleNamespace()
    return furnace, SimpleNamespace(userdata=hosted_furnace.initial_state())


class FurnaceReachesDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_the_last_safety_answer_says_to_dispatch_now(self):
        # The 2026-11-02 eval call: every field recorded, the vulnerable answer last.
        furnace, context = line()
        with patch.object(agent, "publish_ui_event"):
            await furnace.update_ticket(context, problem="furnace stopped, blowing cold air")
            await furnace.update_ticket(context, address="42 Maple Crescent, Barrie")
            await furnace.update_ticket(context, callback_number="7055550193")
            self.assertIn(
                "still needed: safety", await furnace.update_ticket(context, gas_smell=False)
            )
            last = await furnace.update_ticket(context, vulnerable_occupant=True)
            self.assertIn("call dispatch_technician now", last)
            dispatched = await furnace.dispatch_technician(context)
        self.assertRegex(dispatched, r"^Dispatched\. Ticket BHL-[0-9A-F]{5}, .*within 2 hours\.$")

    async def test_a_read_back_in_the_final_update_is_confirmed_first(self):
        furnace, context = line()
        with patch.object(agent, "publish_ui_event"):
            await furnace.update_ticket(
                context,
                problem="no heat",
                address="42 Maple Crescent, Barrie",
                gas_smell=False,
                vulnerable_occupant=False,
            )
            last = await furnace.update_ticket(context, callback_number="705 555 0193")
        self.assertIn("read back the digits", last)
        self.assertIn("once they confirm that read-back, call dispatch_technician", last)

    async def test_resent_address_and_number_do_not_ask_for_another_read_back(self):
        # The 2026-11-03 eval call: update_ticket six times, each re-sending the
        # address and number, so every result asked for a read-back and it never dispatched.
        furnace, context = line()
        with patch.object(agent, "publish_ui_event"):
            await furnace.update_ticket(
                context,
                problem="no heat",
                address="42 Maple Crescent, Barrie",
                callback_number="705 555 0193",
                gas_smell=False,
            )
            last = await furnace.update_ticket(
                context,
                address="42 Maple Crescent, Barrie",
                callback_number="705 555 0193",
                vulnerable_occupant=False,
            )
        self.assertNotIn("read back", last)
        self.assertIn("call dispatch_technician now", last)

    def test_a_gas_smell_never_points_to_dispatch(self):
        ticket = {"problem": "x", "address": "1 A St", "callback": "y", "safety": "z", "gas": True}
        self.assertIn("do not dispatch", agent.next_step(ticket))
        self.assertNotIn("dispatch_technician", agent.next_step(ticket))


if __name__ == "__main__":
    unittest.main()
