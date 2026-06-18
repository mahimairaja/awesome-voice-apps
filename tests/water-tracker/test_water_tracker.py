from livekit.agents import AgentSession
from livekit.agents.evals import (
    JudgeGroup,
    relevancy_judge,
    task_completion_judge,
    tool_use_judge,
)
from livekit.plugins import openai

from agent import DEFAULT_GOAL, WaterCoach


async def test_logs_water_and_changes_goal(fake_room, judge_llm):
    userdata = {"glasses": 0, "goal": DEFAULT_GOAL}
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini"), userdata=userdata) as session:
        await session.start(WaterCoach(fake_room))

        await session.run(user_input="I just drank two glasses of water.")
        await session.run(user_input="Change my daily goal to ten glasses.")

        judges = JudgeGroup(
            llm=judge_llm,
            judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
        )
        evaluation = await judges.evaluate(session.history)
        assert evaluation.all_passed, {
            name: j.reasoning for name, j in evaluation.judgments.items()
        }
