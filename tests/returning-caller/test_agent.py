from types import SimpleNamespace
from unittest.mock import patch

import agent
import memory
import pytest


@pytest.fixture
def line(tmp_path):
    concierge = agent.Concierge(SimpleNamespace(), agent.FileStore(tmp_path / "caller.json"))
    return concierge, SimpleNamespace(userdata=agent.initial_state())


@pytest.fixture(autouse=True)
def quiet():
    with patch.object(agent, "publish_ui_event") as publish:
        yield publish


async def test_asks_before_keeping_then_keeps_with_an_expiry(line):
    concierge, ctx = line
    refused = await concierge.remember(ctx, "context", "Flying to Lisbon on the 20th.", "Card.")
    assert refused == "not kept: no consent on file"
    assert "recorded" in await concierge.record_consent(ctx, True)
    kept = await concierge.remember(ctx, "context", "Flying to Lisbon on the 20th.", "Card.")
    assert kept.startswith("kept as m") and kept.endswith("for 3 days")
    assert len(ctx.userdata["fresh"]) == 2


async def test_sensitive_note_is_refused_and_shown_masked(line, quiet):
    concierge, ctx = line
    await concierge.record_consent(ctx, True)
    said = await concierge.remember(ctx, "context", "Card 4520 1234 5678 9012 is new.", "x")
    assert said.startswith("not kept: account, card or phone numbers")
    assert ctx.userdata["refused"] == [
        {"text": "Card [number removed] is new.", "reason": "account, card or phone numbers"}
    ]
    props = quiet.call_args.args[2]
    assert "4520" not in str(props)


async def test_second_call_starts_from_the_file(tmp_path):
    store = agent.FileStore(tmp_path / "caller.json")
    first = agent.Concierge(SimpleNamespace(), store)
    ctx = SimpleNamespace(userdata=agent.initial_state())
    with patch.object(agent, "publish_ui_event"):
        await first.record_consent(ctx, True)
        await first.remember(ctx, "task", "Owed a call about the tuition wire.", "Follow up.")
    second = agent.Concierge(SimpleNamespace(), store)
    entries = await second.load_file()
    assert "Owed a call about the tuition wire." in second.instructions
    assert "returning" in agent.greeting(entries, memory.consent(entries, 0)["saved"])
    assert agent.initial_state(entries)["loaded"] == 1


async def test_forget_that_and_forget_everything(line):
    concierge, ctx = line
    await concierge.record_consent(ctx, True)
    await concierge.remember(ctx, "preference", "Prefers a text over a call.", "So we text.")
    note = ctx.userdata["entries"][-1]["id"]
    assert "no such note" in await concierge.forget(ctx, "m000000")
    assert "deleted" in await concierge.forget(ctx, note)
    assert ctx.userdata["entries"][-1]["text"] is None
    await concierge.remember(ctx, "task", "Owed a callback.", "Why.")
    assert "everything" in await concierge.forget_everything(ctx)
    assert memory.active(ctx.userdata["entries"], 1e12) == []
    # Saying no to consent later withdraws nothing that is not there.
    assert "nothing kept" in await concierge.record_consent(ctx, False)


async def test_summary_only_with_consent_and_once(line):
    concierge, ctx = line
    concierge._state = ctx.userdata
    ctx.userdata["lines"] = ["Client: hi", "Concierge: hello", "Client: wire status?"]
    with patch.object(agent, "summarize", return_value=("Asked about a wire.", 50, 10)) as llm:
        await concierge.write_summary()
        llm.assert_not_called()
    concierge._summary_task = None
    await concierge.record_consent(ctx, True)
    with patch.object(agent, "summarize", return_value=("Asked about a wire.", 50, 10)) as llm:
        await concierge.write_summary()
        await concierge.write_summary()
        llm.assert_called_once()
    assert memory.summary(ctx.userdata["entries"], 0)["text"] == "Asked about a wire."


def test_caller_key_is_a_safe_file_name():
    assert agent.caller_key("sip_+14165550199") == "sip__14165550199"
    assert agent.caller_key("../../etc") == "______etc"
    assert agent.caller_key("") == "console"
