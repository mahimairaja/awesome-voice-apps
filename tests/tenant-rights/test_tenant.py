import os
from types import SimpleNamespace

import pytest
from livekit.agents import AgentSession
from livekit.agents.evals import (
    JudgeGroup,
    coherence_judge,
    relevancy_judge,
    task_completion_judge,
)
from livekit.plugins import openai

import agent as tenant
from agent import NIM_BASE_URL, NIM_LLM_MODEL, RentersGuide


async def test_answers_grounded_in_a_passage(fake_room, judge_llm, monkeypatch):
    nvidia_key = os.environ.get("NVIDIA_API_KEY")
    if not nvidia_key:
        pytest.skip("NVIDIA_API_KEY not set; tenant-rights runs on NVIDIA NIM")

    # Skip the embeddings index entirely: stub retrieval with one canned HUD
    # passage so the eval exercises the agent's grounded-answer behavior, not the
    # vector store. embed_query and retrieve are module globals in agent.py.
    async def _fake_embed_query(question):
        return [0.0]

    def _fake_retrieve(index, query_vec, k=3, floor=0.0):
        hit = SimpleNamespace(
            source_label="HUD renter guidance",
            text=(
                "Security deposit returns\nAfter a tenant moves out, many states "
                "require the landlord to return the security deposit, minus any "
                "lawful deductions, within roughly two to four weeks."
            ),
        )
        return SimpleNamespace(covered=True, hits=[hit])

    monkeypatch.setattr(tenant, "embed_query", _fake_embed_query)
    monkeypatch.setattr(tenant, "retrieve", _fake_retrieve)

    agent_llm = openai.LLM(model=NIM_LLM_MODEL, base_url=NIM_BASE_URL, api_key=nvidia_key)
    async with AgentSession(llm=agent_llm) as session:
        await session.start(RentersGuide(fake_room, index={}, floor=0.0))

        await session.run(user_input="How long does my landlord have to return my deposit?")

        judges = JudgeGroup(
            llm=judge_llm,
            judges=[task_completion_judge(), relevancy_judge(), coherence_judge()],
        )
        evaluation = await judges.evaluate(session.history)
        assert evaluation.all_passed, {
            name: j.reasoning for name, j in evaluation.judgments.items()
        }
