from livekit.agents import AgentSession
from livekit.agents.evals import (
    JudgeGroup,
    relevancy_judge,
    task_completion_judge,
    tool_use_judge,
)
from livekit.plugins import openai

from agent import FurnaceLine, new_ticket


async def test_takes_a_paused_address_and_dispatches(fake_room, judge_llm):
    userdata = new_ticket()
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini"), userdata=userdata) as session:
        await session.start(FurnaceLine(fake_room))

        await session.run(user_input="My furnace just died, there's no heat at all.")
        await session.run(
            user_input="It's 42... uh... Maple Street in Barrie. Callback is 705 555 0142."
        )
        await session.run(user_input="Yes that's right. No gas smell, but my mom is 88.")
        await session.run(user_input="Yes, please send someone.")

        assert userdata["ticket"]["address"].startswith("42 Maple")
        assert userdata["ref"] is not None
        judges = JudgeGroup(
            llm=judge_llm,
            judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
        )
        evaluation = await judges.evaluate(session.history)
        assert evaluation.all_passed, {
            name: j.reasoning for name, j in evaluation.judgments.items()
        }
