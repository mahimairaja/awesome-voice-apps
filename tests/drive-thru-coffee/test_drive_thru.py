from livekit.agents import AgentSession
from livekit.agents.evals import (
    JudgeGroup,
    relevancy_judge,
    task_completion_judge,
    tool_use_judge,
)
from livekit.plugins import openai

from agent import DriveThruAttendant


async def test_takes_and_submits_an_order(fake_room, judge_llm):
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini"), userdata={"cart": []}) as session:
        await session.start(DriveThruAttendant(fake_room))

        await session.run(user_input="A large iced latte with oat milk, please.")
        await session.run(user_input="That is everything, and the name is Sam.")

        judges = JudgeGroup(
            llm=judge_llm,
            judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
        )
        evaluation = await judges.evaluate(session.history)
        assert evaluation.all_passed, {
            name: j.reasoning for name, j in evaluation.judgments.items()
        }


def test_only_names_become_cup_names():
    from agent import _looks_like_name

    for name in ("Sam", "Priya.", "It's Sam.", "Mary Ann", "Jean-Luc"):
        assert _looks_like_name(name), name
    for reply in ("No, that's everything.", "That's all, thanks.", "Yes", ""):
        assert not _looks_like_name(reply), reply
