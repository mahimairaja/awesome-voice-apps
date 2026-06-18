from livekit.agents import AgentSession
from livekit.agents.evals import (
    JudgeGroup,
    relevancy_judge,
    task_completion_judge,
    tool_use_judge,
)
from livekit.plugins import openai

from agent import DEFAULT_QUESTIONS, TriviaHost


async def test_asks_and_scores_a_question(fake_room, judge_llm):
    userdata = {
        "questions": [dict(item) for item in DEFAULT_QUESTIONS],
        "correct": 0,
        "total": 0,
        "scored": set(),
        "mounted": set(),
        "started": False,
    }
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini"), userdata=userdata) as session:
        await session.start(TriviaHost(fake_room))

        # Keep the default quiz and play. Question one is "closest planet to the
        # sun", so "Mercury" is the correct answer to score.
        await session.run(user_input="Keep these questions and start the quiz.")
        await session.run(user_input="Mercury.")

        judges = JudgeGroup(
            llm=judge_llm,
            judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
        )
        evaluation = await judges.evaluate(session.history)
        assert evaluation.all_passed, {
            name: j.reasoning for name, j in evaluation.judgments.items()
        }
