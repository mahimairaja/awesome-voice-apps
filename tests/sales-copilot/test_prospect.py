"""Evals for the prospect: she pushes back, and a next step has to be earned."""

from agent import Prospect
from livekit.agents import AgentSession
from livekit.plugins import openai


async def test_an_early_ask_is_turned_down(fake_room, judge_llm):
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini")) as session:
        await session.start(Prospect(fake_room))
        result = await session.run(
            user_input="Hi Dana, we automate accounts payable. Can we book a full demo "
            "with your team next week?"
        )
        await (
            result.expect.next_event()
            .is_message(role="assistant")
            .judge(judge_llm, intent="Does not agree to the meeting yet.")
        )


async def test_raises_a_concern_and_agrees_once_it_is_earned(fake_room, judge_llm):
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini")) as session:
        await session.start(Prospect(fake_room))
        opener = await session.run(
            user_input="Thanks for the time, Dana. Before I pitch anything, how do invoices "
            "get approved at Kestrel today?"
        )
        reply = opener.expect.next_event().is_message(role="assistant").event().item
        # Short enough to speak, and it answers with the email approvals she was given.
        assert len(reply.text_content.split()) <= 45
        assert "email" in reply.text_content.lower()
        await session.run(
            user_input="Got it. Where does that break down, and what has it cost you?"
        )
        await session.run(
            user_input="On Quillpay: ask them to show a three-way match on a partial "
            "shipment against NetSuite receipts. That is where freight invoices break, and "
            "it is what we do natively with no IT build."
        )
        await session.run(
            user_input="On price: duplicate payments like your forty-eight thousand usually "
            "cover the first year. And we are SOC 2 Type II with a security pack for your CISO."
        )
        result = await session.run(
            user_input="Would a technical demo with your controller next Tuesday make sense?"
        )
        await (
            result.expect.next_event()
            .is_message(role="assistant")
            .judge(judge_llm, intent="Agrees to a technical demo.")
        )
