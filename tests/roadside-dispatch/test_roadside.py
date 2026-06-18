from livekit.agents import AgentSession
from livekit.agents.evals import (
    JudgeGroup,
    relevancy_judge,
    task_completion_judge,
    tool_use_judge,
)
from livekit.plugins import openai

from agent import RoadsideAgent
from health import AudioHealth


async def test_captures_details_and_dispatches(fake_room, judge_llm):
    # AudioHealth() with no scores reports a clean line, so fields capture
    # straight through (the bad-line gating needs a real audio track, out of
    # scope for a text eval). This exercises the capture -> dispatch flow.
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini")) as session:
        await session.start(RoadsideAgent(fake_room, AudioHealth()))

        await session.run(user_input="I'm on Highway 401 near exit 25 and my car won't start.")
        await session.run(user_input="It's a blue Honda Civic.")
        await session.run(user_input="The plate is A B C 1 2 3.")
        await session.run(user_input="You can reach me at 555 010 2020.")
        await session.run(user_input="Yes, that's all correct. Please send help.")

        judges = JudgeGroup(
            llm=judge_llm,
            judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
        )
        evaluation = await judges.evaluate(session.history)
        assert evaluation.all_passed, {
            name: j.reasoning for name, j in evaluation.judgments.items()
        }
