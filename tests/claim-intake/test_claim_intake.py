from livekit.agents import AgentSession
from livekit.agents.evals import (
    JudgeGroup,
    relevancy_judge,
    task_completion_judge,
    tool_use_judge,
)
from livekit.plugins import google

from agent import ClaimIntake


async def test_captures_and_files_a_claim(fake_room, judge_llm):
    userdata = {"claim": {}, "claim_ref": None}
    async with AgentSession(
        llm=google.LLM(model="gemini-3-flash-preview"), userdata=userdata
    ) as session:
        await session.start(ClaimIntake(fake_room))

        await session.run(user_input="Hi, my name is Dana Reyes.")
        await session.run(user_input="My policy number is A B 1 2 3 4 5 6.")
        await session.run(user_input="The accident was on March 3rd, 2026.")
        await session.run(user_input="It happened on Highway 101 near the Oak Street exit.")
        await session.run(user_input="I was driving a 2021 blue Honda Civic.")
        await session.run(user_input="Someone rear-ended me while I was stopped at a red light.")
        await session.run(user_input="No, nobody was hurt.")
        await session.run(user_input="Yes, the car is still drivable.")
        await session.run(user_input="Yes, that is all correct. Please file the claim.")

        judges = JudgeGroup(
            llm=judge_llm,
            judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
        )
        evaluation = await judges.evaluate(session.history)
        assert evaluation.all_passed, {
            name: j.reasoning for name, j in evaluation.judgments.items()
        }
