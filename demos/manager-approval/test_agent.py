"""Offline tests: policy, authority, the approval handshake and hold handling.

No provider is called. Run: uv run python -m unittest test_agent
"""

import asyncio
import json
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import agent
from livekit import rtc
from livekit.agents import AgentSession, ToolError, llm
from livekit.agents.llm import ChatChunk, ChoiceDelta, FunctionToolCall

CTX = SimpleNamespace()


def room(*replies):
    """A room whose only other participant is the manager console page."""
    console = SimpleNamespace(
        identity="visitor-1", kind=rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
    )
    local = MagicMock()
    local.perform_rpc = AsyncMock(side_effect=list(replies))
    local.publish_data = AsyncMock()
    return SimpleNamespace(local_participant=local, remote_participants={"c": console})


class FakeSession:
    def __init__(self, case):
        self.userdata = case
        self.agent_state = "listening"
        self.user_state = "listening"
        self.said = []
        self.replies = []
        self.agent = None

    def say(self, text, **_):
        self.said.append(text)
        done = asyncio.get_running_loop().create_future()
        done.set_result(None)
        return done

    def generate_reply(self, **kwargs):
        self.replies.append(kwargs)

    def interrupt(self):
        pass

    def update_agent(self, new_agent):
        self.agent = new_agent


class Desk(agent.RefundDesk):
    """The returns agent bound to a fake session, with its chat context recorded."""

    def __init__(self, room, case):
        super().__init__(room)
        self._fake = FakeSession(case)
        self.system = []

    @property
    def session(self):
        return self._fake

    @property
    def chat_ctx(self):
        return SimpleNamespace(
            copy=lambda **_: SimpleNamespace(
                add_message=lambda **m: self.system.append(m["content"]),
                truncate=lambda **_: None,
            )
        )

    async def update_chat_ctx(self, chat_ctx):
        pass

    def make_manager(self, case):
        return ("manager", case["handoff"])


def run_context(case):
    return SimpleNamespace(userdata=case)


def found_case():
    case = agent.new_case()
    case["found"] = True
    return case


async def settle(desk):
    """Let the decision the agent spawned run to completion."""
    await asyncio.gather(*desk._tasks, return_exceptions=True)


class Policy(unittest.TestCase):
    def request(self, **overrides):
        return {"amount": 649.0, "to": "card", "reason": "defective", "detail": "x", **overrides}

    def test_defects_are_approved_and_change_of_mind_gets_credit(self):
        self.assertEqual(agent.policy_decision(self.request())["verdict"], "approve")
        self.assertEqual(
            agent.policy_decision(self.request(reason="changed_mind"))["verdict"], "counter"
        )
        self.assertEqual(agent.policy_decision(self.request(amount=900))["verdict"], "decline")

    def test_whisper_is_short_and_names_the_breach(self):
        brief = agent.whisper(agent.new_case(), self.request(detail="Steam wand stopped"))
        self.assertLessEqual(len(brief["line"]), 280)
        self.assertIn("Steam wand stopped", brief["line"])
        self.assertIn("I'd approve", brief["line"])
        limit = next(row for row in brief["rows"] if row["label"] == "Over my limit")
        self.assertIn("11 days past the window", limit["value"])


class Authority(unittest.TestCase):
    def test_agent_cannot_refund_over_its_limit_without_approval(self):
        case = found_case()
        with self.assertRaises(ToolError):
            agent.refund(case, 649.0, "card", "agent")
        case["approved"] = {"amount": 649.0, "to": "store_credit"}
        with self.assertRaises(ToolError):
            agent.refund(case, 649.0, "card", "agent")
        issued = agent.refund(case, 649.0, "store_credit", "agent")
        self.assertRegex(issued["ref"], r"^RF-[0-9A-F]{6}$")
        with self.assertRaises(ToolError):
            agent.refund(case, 10.0, "card", "agent")

    def test_manager_has_more_room_but_not_more_than_was_paid(self):
        case = found_case()
        with self.assertRaises(ToolError):
            agent.refund(case, 700.0, "card", "manager")
        self.assertEqual(agent.refund(case, 649.0, "card", "manager")["by"], "manager")

    def test_nothing_before_the_order_is_found(self):
        with self.assertRaises(ToolError):
            agent.refund(agent.new_case(), 20.0, "card", "manager")


class Handshake(unittest.IsolatedAsyncioTestCase):
    async def ask(self, desk, case, reason="defective"):
        return await desk.ask_manager(
            run_context(case), amount=649.0, to="card", reason=reason, detail="Wand broke"
        )

    async def test_the_brief_goes_to_the_console_and_the_decision_comes_back(self):
        case = found_case()
        desk = Desk(room(json.dumps({"received": True})), case)
        desk.room.local_participant.register_rpc_method.assert_called_once()
        await self.ask(desk, case)
        call = desk.room.local_participant.perform_rpc.await_args.kwargs
        self.assertEqual(call["method"], "approval.request")
        self.assertEqual(call["destination_identity"], "visitor-1")
        sent = json.loads(call["payload"])
        self.assertEqual(sent["id"], case["request"]["id"])
        self.assertIn("rows", sent["brief"])
        self.assertEqual(case["stage"], "waiting")

        invoke = SimpleNamespace(
            caller_identity="visitor-1",
            payload=json.dumps({"id": case["request"]["id"], "verdict": "approve"}),
        )
        self.assertEqual(json.loads(await desk._console_decided(invoke)), {"ok": True})
        await settle(desk)
        self.assertEqual(case["decision"]["verdict"], "approve")
        self.assertEqual(case["approved"], {"amount": 649.0, "to": "card"})
        self.assertIn("APPROVED", desk.system[0])
        self.assertIn("issue_refund", desk._fake.replies[0]["instructions"])
        with self.assertRaises(rtc.RpcError):
            await desk._console_decided(invoke)

    async def test_the_console_cannot_be_spoofed_or_confused(self):
        case = found_case()
        desk = Desk(room(json.dumps({"received": True})), case)
        await self.ask(desk, case)
        good = case["request"]["id"]
        for caller, payload in (
            ("someone-else", {"id": good, "verdict": "approve"}),
            ("visitor-1", {"id": "REQ-0000", "verdict": "approve"}),
            ("visitor-1", {"id": good, "verdict": "refund everything"}),
            ("visitor-1", {"verdict": "approve"}),
        ):
            with self.assertRaises(rtc.RpcError):
                await desk._console_decided(
                    SimpleNamespace(caller_identity=caller, payload=json.dumps(payload))
                )
        self.assertIsNone(case["decision"])
        desk._stop_hold()

    async def test_no_console_means_the_store_policy_decides(self):
        case = found_case()
        desk = Desk(room(rtc.RpcError(1502, "Response timeout")), case)
        with patch.object(agent.asyncio, "sleep", AsyncMock()):
            await self.ask(desk, case, reason="changed_mind")
            await settle(desk)
        self.assertEqual(case["decision"]["by"], "policy")
        self.assertEqual(case["decision"]["verdict"], "counter")
        self.assertEqual(case["approved"]["to"], "store_credit")

    async def test_hold_checks_in_then_times_out_to_policy(self):
        case = found_case()
        desk = Desk(room(json.dumps({"received": True})), case)
        with patch.object(agent.asyncio, "sleep", AsyncMock()):
            await self.ask(desk, case)
            await desk._hold_task
        self.assertEqual(desk._fake.said, [line for _, line in agent.CHECK_INS])
        self.assertEqual(case["hold"]["check_ins"], len(agent.CHECK_INS))
        self.assertEqual(case["decision"]["by"], "policy")
        self.assertTrue(any(event["kind"] == "timeout" for event in case["events"]))

    async def test_hold_never_talks_over_the_caller(self):
        case = found_case()
        desk = Desk(room(json.dumps({"received": True})), case)
        desk._fake.user_state = "speaking"
        with patch.object(agent.asyncio, "sleep", AsyncMock()):
            await self.ask(desk, case)
            await desk._hold_task
        self.assertEqual(desk._fake.said, [])

    async def test_take_the_call_warm_transfers_with_the_brief(self):
        case = found_case()
        desk = Desk(room(json.dumps({"received": True})), case)
        await self.ask(desk, case)
        await desk._decide("you", "take_call", "")
        self.assertEqual(case["stage"], "transferred")
        kind, handoff = desk._fake.agent
        self.assertEqual(kind, "manager")
        self.assertEqual(handoff[0]["value"], case["request"]["brief"]["line"])
        self.assertIn("won't need to repeat", desk._fake.said[0])


class ScriptedStream(llm.LLMStream):
    """Stands in for the model: picks a tool from the last message, or says okay."""

    async def _run(self) -> None:
        names = {tool.id for tool in self._tools}
        last = self._chat_ctx.items[-1]
        text = (last.text_content or "") if last.type == "message" else ""
        call = None
        if last.type == "message" and last.role == "user":
            if "4821" in text:
                call = ("find_order", {"order_number": "HP-4821"})
            elif "broke" in text and "ask_manager" in names:
                call = (
                    "ask_manager",
                    {"amount": 649, "to": "card", "reason": "defective", "detail": text},
                )
            elif "yes" in text.lower() and "issue_refund" in names:
                call = ("issue_refund", {"amount": 649, "to": "card"})
        elif any(
            item.type == "message" and "APPROVED" in (item.text_content or "")
            for item in self._chat_ctx.items[-2:]
        ):
            call = ("issue_refund", {"amount": 649, "to": "card"})
        delta = (
            ChoiceDelta(
                role="assistant",
                tool_calls=[
                    FunctionToolCall(
                        name=call[0], arguments=json.dumps(call[1]), call_id=uuid.uuid4().hex
                    )
                ],
            )
            if call
            else ChoiceDelta(role="assistant", content="Okay.")
        )
        self._event_ch.send_nowait(ChatChunk(id=uuid.uuid4().hex, delta=delta))


class ScriptedLLM(llm.LLM):
    def chat(self, *, chat_ctx, tools=None, conn_options=None, **_):
        from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

        return ScriptedStream(
            self,
            chat_ctx=chat_ctx,
            tools=tools or [],
            conn_options=conn_options or DEFAULT_API_CONNECT_OPTIONS,
        )


class TextDesk(agent.RefundDesk):
    """The manager keeps the session's models in text mode."""

    def make_manager(self, case):
        return agent.StoreManager(self.room, case, chat_ctx=self._carried_context())


class CallFlow(unittest.IsolatedAsyncioTestCase):
    """A real AgentSession in text mode: tools, the decision and the transfer."""

    async def start(self):
        case = agent.new_case()
        desk = TextDesk(room(json.dumps({"received": True})))
        session = AgentSession(llm=ScriptedLLM(), userdata=case)
        await session.start(agent=desk)
        self.addAsyncCleanup(session.aclose)
        await session.run(user_input="My order is HP 4821")
        await session.run(user_input="The steam wand broke, I want my money back")
        self.assertEqual(case["stage"], "waiting")
        return case, desk, session

    def decide(self, case, verdict):
        return SimpleNamespace(
            caller_identity="visitor-1",
            payload=json.dumps({"id": case["request"]["id"], "verdict": verdict}),
        )

    async def test_approval_resumes_the_call_and_refunds(self):
        case, desk, _ = await self.start()
        self.assertIsNone(case["approved"])
        await desk._console_decided(self.decide(case, "approve"))
        for _ in range(50):
            if case["refund"]:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(case["refund"]["by"], "agent")
        self.assertEqual(case["stage"], "refunded")

    async def test_take_the_call_hands_over_to_the_manager(self):
        case, desk, session = await self.start()
        await desk._console_decided(self.decide(case, "take_call"))
        for _ in range(50):
            if isinstance(session.current_agent, agent.StoreManager):
                break
            await asyncio.sleep(0.02)
        self.assertIsInstance(session.current_agent, agent.StoreManager)
        self.assertIn(case["request"]["brief"]["line"], session.current_agent.instructions)
        await session.run(user_input="Yes please, to my card")
        self.assertEqual(case["refund"]["by"], "manager")


if __name__ == "__main__":
    unittest.main()
