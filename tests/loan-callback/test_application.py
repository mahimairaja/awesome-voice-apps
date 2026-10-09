import json
from types import SimpleNamespace

import application
import pytest
from agent import FileStore, LoanCallback
from livekit.agents import llm

ANSWERS = {
    "owner_name": "Priya Shah",
    "business_name": "Shah Bakery LLC",
    "years_in_business": "6",
    "annual_revenue": "1.2 million",
    "loan_amount": "250,000",
    "use_of_funds": "a second oven and a delivery van",
    "monthly_debt": "4200",
}


def answered(count: int) -> dict:
    state = application.new_application()
    for field in application.FIELDS[:count]:
        assert not application.record(state, field, ANSWERS[field]).startswith("rejected")
    return state


def test_answers_are_normalized_and_the_next_question_follows():
    state = application.new_application()
    result = application.record(state, "owner_name", "  Priya   Shah ", "Prefers mornings.")
    assert result.startswith("recorded owner: Priya Shah.")
    assert application.PROMPTS["business_name"] in result
    application.record(state, "annual_revenue", "1.2 million")
    application.record(state, "loan_amount", "$250k")
    assert state["answers"]["annual_revenue"] == "$1,200,000"
    assert state["answers"]["loan_amount"] == "$250,000"
    assert state["notes"] == ["Prefers mornings."]
    # Answered out of order: the caller is still on the first unanswered question.
    assert application.current(state) == "business_name"


def test_bad_values_are_rejected_without_changing_state():
    state = application.new_application()
    for field, value in [
        ("loan_amount", "a lot"),
        ("loan_amount", "50"),
        ("years_in_business", "400"),
        ("owner_name", "x" * 200),
        ("credit_score", "800"),
    ]:
        assert application.record(state, field, value).startswith("rejected")
    assert state["answers"] == {}


def test_notes_roll_over_keeping_the_latest():
    state = application.new_application()
    for i in range(10):
        application.record(state, "owner_name", "Priya Shah", f"note {i}")
    assert state["notes"] == [f"note {i}" for i in range(4, 10)]


def test_submit_needs_every_answer_and_happens_once():
    state = answered(6)
    assert "monthly debt" in application.submit(state)
    application.record(state, "monthly_debt", "4200")
    first = application.submit(state)
    assert first.startswith(f"submitted. Reference {state['app_id']}")
    assert state["submitted"]["decision"] == "Ready for underwriting"
    assert application.submit(state).startswith("already submitted")
    assert application.record(state, "loan_amount", "300000").startswith("rejected")


def test_large_loans_are_referred():
    state = answered(7)
    application.record(state, "loan_amount", "900000")
    application.submit(state)
    assert state["submitted"]["decision"].startswith("Referred")


def test_resume_continues_an_open_application_at_the_same_question():
    state = answered(3)
    state["version"] = 3
    saved = json.loads(json.dumps(state))
    resumed, ok = application.resume(saved)
    assert ok
    assert resumed["call"] == 2
    assert application.current(resumed) == "annual_revenue"
    message = application.context_message(resumed)
    assert "Owner: Priya Shah" in message
    assert application.PROMPTS["annual_revenue"] in message


def test_resume_refuses_tampered_or_finished_checkpoints():
    for bad in [
        None,
        "state",
        {**answered(1), "app_id": "<script>"},
        {**answered(1), "answers": {"ssn": "123"}},
        {**answered(1), "notes": ["x" * 500]},
        {**answered(1), "version": -1},
        {**answered(1), "call": True},
    ]:
        state, ok = application.resume(bad)
        assert not ok
        assert state["answers"] == {}
    done = answered(7)
    done["version"] = 9
    application.submit(done)
    fresh, ok = application.resume(done)
    assert not ok
    assert fresh["answers"] == {} and fresh["submitted"] is None
    # Versions keep rising so a store never accepts an older write over a newer one.
    assert fresh["version"] == 9


def test_snapshot_marks_done_current_and_todo():
    view = application.snapshot(answered(2))
    statuses = [q["status"] for q in view["questions"]]
    assert statuses == ["done", "done", "current", "todo", "todo", "todo", "todo"]


@pytest.mark.asyncio
async def test_file_store_round_trip_and_resume(tmp_path):
    store = FileStore(tmp_path, "visitor/../../etc")
    assert store.path.parent == tmp_path
    agent = LoanCallback(SimpleNamespace(), store)
    await agent.restore()
    assert not agent.resumed
    agent.publish = lambda: None
    await agent.record_answer("owner_name", "Priya Shah", "")
    await agent.record_answer("business_name", "Shah Bakery LLC", "Wants funds before March.")
    assert agent.saved["version"] == 2 and agent.saved["ok"]

    callback = LoanCallback(SimpleNamespace(), FileStore(tmp_path, "visitor/../../etc"))
    await callback.restore()
    assert callback.resumed
    assert callback.state["answers"]["business_name"] == "Shah Bakery LLC"
    assert "Wants funds before March." in callback.carried["text"]
    assert "welcome Priya back" in callback.opening()
    assert application.PROMPTS["years_in_business"] in callback.opening()


def test_compaction_keeps_instructions_state_recent_turns_and_turn_instructions():
    agent = LoanCallback(SimpleNamespace())
    agent.state = answered(2)
    agent._prior_tokens = 300
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="system", content=["You take loan applications."])
    for i in range(10):
        ctx.add_message(role="assistant", content=[f"Question {i}?"])
        ctx.add_message(role="user", content=[f"Answer {i}"])
    ctx.add_message(role="system", content=["Say goodbye."])
    compact, dropped = agent.compact(ctx)
    texts = [item.text_content for item in compact.items]
    assert texts[0] == "You take loan applications."
    assert texts[1].startswith("Application state:")
    assert "Owner: Priya Shah" in texts[1]
    assert texts[-1] == "Say goodbye."
    assert len(compact.items) == 2 + 6 + 1
    assert texts[-2] == "Answer 9"
    assert dropped > 300


def test_compaction_never_starts_on_an_orphaned_tool_result():
    agent = LoanCallback(SimpleNamespace())
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="system", content=["Instructions."])
    for i in range(4):
        ctx.add_message(role="user", content=[f"Answer {i}"])
        ctx.items.append(llm.FunctionCall(call_id=f"c{i}", name="record_answer", arguments="{}"))
        ctx.items.append(
            llm.FunctionCallOutput(
                call_id=f"c{i}", name="record_answer", output="ok", is_error=False
            )
        )
    compact, _ = agent.compact(ctx)
    assert compact.items[2].type == "message"


def test_compaction_shrinks_to_fit_the_budget(monkeypatch):
    import agent as module

    monkeypatch.setattr(module, "TOKEN_BUDGET", 60)
    agent = LoanCallback(SimpleNamespace())
    ctx = llm.ChatContext.empty()
    ctx.add_message(role="system", content=["Instructions."])
    for i in range(6):
        ctx.add_message(role="user", content=["word " * 40])
    compact, _ = agent.compact(ctx)
    assert len(compact.items) == 2 + 2


def test_budget_pairs_metrics_with_the_request_that_sent_them():
    agent = LoanCallback(SimpleNamespace())
    agent.publish = lambda: None
    agent._pending = [500, 900]
    metrics = SimpleNamespace(prompt_tokens=700)
    from livekit.agents.metrics import LLMMetrics

    real = LLMMetrics(
        label="openai",
        request_id="r",
        timestamp=0,
        duration=1,
        ttft=0.2,
        cancelled=False,
        completion_tokens=20,
        prompt_tokens=700,
        prompt_cached_tokens=0,
        total_tokens=720,
        tokens_per_second=20,
    )
    agent.on_llm_metrics(metrics)  # not LLM metrics: ignored
    agent.on_llm_metrics(real)
    assert agent.budget == [{"sent": 700, "full": 1200}]
    assert agent._pending == [900]
