from livekit.agents import AgentSession
from livekit.agents.evals import (
    JudgeGroup,
    relevancy_judge,
    task_completion_judge,
    tool_use_judge,
)
from livekit.plugins import cerebras

from agent import PanelScribe, PyannoteLive


async def test_recap_attributes_feedback_by_speaker(fake_room, judge_llm):
    # The sidecar is never connected: the eval feeds turns already labeled with
    # the speaker prefix that reconciliation would produce, so display_speaker
    # is not exercised. Only the trio (the recap behavior) is under test.
    sidecar = PyannoteLive(api_key="")
    async with AgentSession(llm=cerebras.LLM(model="llama-3.3-70b")) as session:
        await session.start(PanelScribe(fake_room, sidecar))

        # Three pre-labeled feedback turns. The scribe stays silent on each.
        await session.run(user_input="[Speaker 1] The candidate was strong on system design.")
        await session.run(user_input="[Speaker 2] I am worried the testing story was thin.")
        await session.run(user_input="[Speaker 3] Communication was fine, nothing stood out.")

        # The trigger turn: the scribe calls publish_scorecard and speaks a recap.
        result = await session.run(user_input="scribe, give us the recap")
        result.expect.contains_function_call(name="publish_scorecard")

        judges = JudgeGroup(
            llm=judge_llm,
            judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
        )
        evaluation = await judges.evaluate(session.history)
        assert evaluation.all_passed, {
            name: j.reasoning for name, j in evaluation.judgments.items()
        }
