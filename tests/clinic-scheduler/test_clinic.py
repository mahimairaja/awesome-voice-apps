from livekit.agents import AgentSession
from livekit.agents.evals import (
    JudgeGroup,
    relevancy_judge,
    task_completion_judge,
    tool_use_judge,
)
from livekit.plugins import openai

from agent import ClinicScheduler, _build_slots


async def test_finds_slots_and_books(fake_room, judge_llm):
    userdata = {"available_slots": _build_slots(), "booking": None, "ui_mounted": set()}
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini"), userdata=userdata) as session:
        await session.start(ClinicScheduler(fake_room))

        await session.run(user_input="What appointments are open this week?")
        await session.run(user_input="Book me the earliest one. My name is Alex Reed.")

        judges = JudgeGroup(
            llm=judge_llm,
            judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
        )
        evaluation = await judges.evaluate(session.history)
        assert evaluation.all_passed, {
            name: j.reasoning for name, j in evaluation.judgments.items()
        }
