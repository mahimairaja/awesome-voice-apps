"""Offline tests for the manager approval demo; never call provider APIs."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import hosted
import hosted_approval
from livekit.agents import ToolError

ID = "598cd768-86d4-42a1-bb44-adc44fba4207"
ROOM = f"playground-{ID}"
KEYS = {"DEEPGRAM_API_KEY": "offline", "OPENAI_API_KEY": "offline", "CARTESIA_API_KEY": "offline"}


def build():
    hosted.claims[ROOM] = {"id": ID, "seconds": 120}
    room = SimpleNamespace(name=ROOM, local_participant=MagicMock(), remote_participants={})
    with (
        patch.dict(hosted.os.environ, KEYS),
        patch.object(hosted, "get_job_context", return_value=SimpleNamespace(room=room)),
    ):
        return hosted.HostedApproval(), room


class ApprovalDemo(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        hosted.claims.clear()

    async def test_registered_with_its_own_budget_and_state(self):
        self.assertIs(hosted.CASCADE_AGENTS["approval"], hosted.HostedApproval)
        self.assertEqual(hosted.HostedApproval.llm_budget, 24)
        agent = object.__new__(hosted.HostedApproval)
        first, second = agent.initial_state(), agent.initial_state()
        self.assertIsNot(first, second)
        self.assertEqual(first["stage"], "intake")

    async def test_answers_the_console_on_sonic_3(self):
        agent, room = build()
        room.local_participant.register_rpc_method.assert_called_once()
        self.assertEqual(
            room.local_participant.register_rpc_method.call_args.args[0], "approval.decide"
        )
        self.assertEqual(agent.tts.model, "cartesia/sonic-3")
        await agent.llm.aclose()

    async def test_the_manager_shares_the_call_caps(self):
        agent, _ = build()
        case = agent.initial_state()
        case["handoff"] = [{"label": "Brief", "value": "x"}]
        with (
            patch.object(type(agent), "chat_ctx", new=MagicMock()),
            patch.dict(hosted.os.environ, KEYS),
        ):
            manager = agent.make_manager(case)
        self.assertIsInstance(manager, hosted_approval.HostedManager)
        self.assertIs(manager.llm, agent.llm)
        self.assertEqual(manager.tts.model, "cartesia/sonic-3")
        self.assertNotEqual(manager.tts._opts.voice, agent.tts._opts.voice)

        agent._finish = AsyncMock()
        agent._llm_requests = agent.llm_budget
        context = SimpleNamespace(to_dict=dict)
        self.assertEqual([c async for c in manager.llm_node(context, [], None)], [])
        await asyncio.sleep(0)
        agent._finish.assert_awaited_once()

        async def text():
            yield "a" * 3900
            yield "b" * 200

        spoken = [chunk async for chunk in agent.bound_text(text())]
        self.assertEqual(sum(map(len, spoken)), 3900)
        await agent.llm.aclose()

    async def test_no_refund_or_approval_while_the_manager_has_not_decided(self):
        agent, _ = build()
        case = agent.initial_state()
        case["found"] = True
        context = SimpleNamespace(userdata=case)
        case["request"] = {"id": "REQ-0001", "amount": 649.0, "to": "card"}
        with self.assertRaisesRegex(ToolError, "Still waiting for the manager"):
            await agent.issue_refund(context, 649.0, "card")
        self.assertIsNone(case["refund"])
        again = await agent.ask_manager(context, 649.0, "card", "defective", "Stopped heating")
        self.assertTrue(again.startswith("Still waiting for the manager"), again)
        await agent.llm.aclose()

    async def test_a_policy_fallback_is_never_called_a_manager_approval(self):
        agent, _ = build()
        case = agent.initial_state()
        case["found"] = True
        case["request"] = {"id": "REQ-0001", "amount": 649.0, "to": "card"}
        case["hold"] = {"since": 0.0, "check_ins": 0}
        session = SimpleNamespace(
            userdata=case, agent_state="listening", generate_reply=MagicMock()
        )
        system = []
        chat_ctx = SimpleNamespace(
            copy=lambda: SimpleNamespace(add_message=lambda **m: system.append(m["content"]))
        )
        with (
            patch.object(type(agent), "session", new=PropertyMock(return_value=session)),
            patch.object(type(agent), "chat_ctx", new=PropertyMock(return_value=chat_ctx)),
            patch.object(agent, "update_chat_ctx", new=AsyncMock()),
        ):
            await agent._decide("policy", "approve", "Defect inside 90 days.")
        self.assertIn("store's written policy decided: APPROVED $649.00 to card", system[0])
        self.assertNotIn("Manager decision", system[0])
        said = session.generate_reply.call_args.kwargs["instructions"]
        self.assertIn("the store's written policy approved it", said)
        self.assertEqual(case["approved"], {"amount": 649.0, "to": "card"})
        issued = await agent.issue_refund(SimpleNamespace(userdata=case), 649.0, "card")
        self.assertTrue(issued.startswith("Refund RF-"), issued)
        await agent.llm.aclose()


if __name__ == "__main__":
    unittest.main()
