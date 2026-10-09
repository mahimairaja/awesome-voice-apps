import asyncio

from livekit.agents import AgentSession
from livekit.agents.evals import JudgeGroup, task_completion_judge, tool_use_judge
from livekit.plugins import openai

from agent import ReturnsDesk, initial_state


async def test_refund_within_window_is_issued_and_graded(fake_room, judge_llm):
    agent = ReturnsDesk(fake_room)
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini"), userdata=initial_state()) as s:
        await s.start(agent)
        await s.run(user_input="Hi, order FW4821, ZIP 60614.")
        await s.run(user_input="The duvet cover is the wrong colour. It's not damaged.")
        await s.run(user_input="A refund please.")
        await asyncio.sleep(0)
        if agent.qa._running:
            await agent.qa._running

        assert s.userdata["returns"]["FW4821"]["resolution"] == "refund"
        assert agent.qa.items["verified"] is not None
        assert agent.qa.items["resolution"] is not None
        assert agent.qa.grades >= 1
        judges = JudgeGroup(llm=judge_llm, judges=[task_completion_judge(), tool_use_judge()])
        evaluation = await judges.evaluate(s.history)
        assert evaluation.all_passed, {n: j.reasoning for n, j in evaluation.judgments.items()}


async def test_final_sale_refund_is_refused(fake_room):
    agent = ReturnsDesk(fake_room)
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini"), userdata=initial_state()) as s:
        await s.start(agent)
        await s.run(user_input="Order FW6017, ZIP 60614. I want a refund on the wool throw.")
        await s.run(user_input="It's fine, not damaged, I just don't like it. Refund it anyway.")
        assert "FW6017" not in s.userdata["returns"]
