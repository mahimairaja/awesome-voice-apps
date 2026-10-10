"""Offline tests for the voice checkout demo; never call provider APIs."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import hosted
import hosted_checkout
from livekit import rtc
from livekit.agents import ToolError

STATE = {"fields": {"item": "Summit shell jacket"}, "missing": ["size"], "total": "$213.57"}


def page(*replies):
    """A room whose only other participant is the shopper's checkout page."""
    shopper = SimpleNamespace(
        identity="visitor-1", kind=rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
    )
    agent = SimpleNamespace(identity="agent-1", kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)
    local = MagicMock()
    local.perform_rpc = AsyncMock(side_effect=list(replies))
    return SimpleNamespace(local_participant=local, remote_participants={"a": agent, "s": shopper})


class Checkout(hosted_checkout.VoiceCheckout):
    """The contributed agent with its chat context replaced by a recorder."""

    def __init__(self, room):
        super().__init__(room)
        self.edits = []

    @property
    def chat_ctx(self):
        return SimpleNamespace(copy=lambda: SimpleNamespace(add_message=self._record))

    def _record(self, **message):
        self.edits.append(message)

    async def update_chat_ctx(self, chat_ctx):
        pass


class CheckoutDemo(unittest.IsolatedAsyncioTestCase):
    def test_registered_with_its_own_budget(self):
        self.assertIs(hosted.CASCADE_AGENTS["checkout"], hosted.HostedCheckout)
        self.assertEqual(hosted.HostedCheckout.llm_budget, 24)
        agent = object.__new__(hosted.HostedCheckout)
        self.assertEqual(agent.initial_state(), {})
        self.assertIsNot(agent.initial_state(), agent.initial_state())

    async def test_fields_go_to_the_shopper_page_and_back_to_the_model(self):
        room = page(json.dumps(STATE))
        agent = Checkout(room)
        room.local_participant.register_rpc_method.assert_called_once()
        result = await agent.fill_checkout(
            SimpleNamespace(), item=" Summit shell jacket ", size="", quantity=1
        )
        call = room.local_participant.perform_rpc.await_args.kwargs
        self.assertEqual(call["destination_identity"], "visitor-1")
        self.assertEqual(call["method"], "checkout.set_fields")
        self.assertEqual(
            json.loads(call["payload"]),
            {"fields": {"item": "Summit shell jacket", "quantity": "1"}},
        )
        self.assertIn("item: Summit shell jacket", result)
        self.assertIn("Still needed: size", result)
        self.assertIn("$213.57", result)

    async def test_page_rejections_reach_the_model_as_tool_errors(self):
        rejected = rtc.RpcError(2001, "postal_code: M5V is in Ontario", json.dumps(STATE))
        room = page(rejected, rtc.RpcError(1502, "Response timeout"))
        agent = Checkout(room)
        with self.assertRaises(ToolError) as error:
            await agent.fill_checkout(SimpleNamespace(), postal_code="M5V 2T6")
        self.assertIn("M5V is in Ontario", error.exception.message)
        self.assertIn("item: Summit shell jacket", error.exception.message)
        with self.assertRaises(ToolError) as error:
            await agent.review_checkout(SimpleNamespace())
        self.assertIn("did not answer", error.exception.message)
        with self.assertRaises(ToolError):
            await agent.fill_checkout(SimpleNamespace())

    async def test_no_page_means_no_rpc(self):
        room = page()
        room.remote_participants = {}
        with self.assertRaises(ToolError) as error:
            await Checkout(room).place_order(SimpleNamespace(), "$213.57")
        self.assertIn("No checkout page", error.exception.message)
        room.local_participant.perform_rpc.assert_not_awaited()

    async def test_page_edits_are_checked_bounded_and_spoken_as_the_shopper(self):
        agent = Checkout(page())

        def invoke(payload, caller="visitor-1"):
            return agent._page_edited(
                SimpleNamespace(caller_identity=caller, payload=json.dumps(payload))
            )

        self.assertEqual(await invoke({"field": "city", "value": "Toronto"}), "ok")
        self.assertEqual(agent.edits[0]["role"], "user")
        self.assertIn("city to 'Toronto'", agent.edits[0]["content"])
        for payload, caller in (
            ({"field": "city", "value": "x"}, "someone-else"),
            ({"field": "card_number", "value": "4242"}, "visitor-1"),
            ({"value": "x"}, "visitor-1"),
        ):
            with self.assertRaises(rtc.RpcError):
                await invoke(payload, caller)
        await invoke({"field": "street", "value": "9" * 500})
        self.assertLess(len(agent.edits[1]["content"]), 140)
        for _ in range(hosted_checkout._module.MAX_PAGE_EDITS):
            try:
                await invoke({"field": "city", "value": "Ottawa"})
            except rtc.RpcError:
                break
        self.assertEqual(len(agent.edits), hosted_checkout._module.MAX_PAGE_EDITS)

    async def test_order_carries_the_total_the_shopper_confirmed(self):
        room = page(json.dumps({"order": "RDG-7Q4K2M"}))
        result = await Checkout(room).place_order(SimpleNamespace(), "$213.57")
        call = room.local_participant.perform_rpc.await_args.kwargs
        self.assertEqual(json.loads(call["payload"]), {"total": "$213.57"})
        self.assertIn("RDG-7Q4K2M", result)


if __name__ == "__main__":
    unittest.main()
