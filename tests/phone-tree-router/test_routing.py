import math
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent
import routing


def probs(**values: float) -> dict[str, float]:
    base = {key: 0.0 for key in [*routing.DEPARTMENTS, routing.UNCLEAR]}
    return base | values


def test_every_department_has_a_menu_path_and_a_router_score():
    assert routing.QUEUES[-1] == routing.UNCLEAR
    assert routing.ROUTER_SCHEMA["required"] == routing.QUEUES
    for key in routing.DEPARTMENTS:
        path = routing.menu_path(key)
        assert path["department"] == key
        assert path["keys"] and path["total"] > 0
        assert path["steps"][-1] == {
            "kind": "press",
            "text": path["keys"][-1],
            "at": path["steps"][-1]["at"],
            "dur": routing.KEY_SECONDS,
        }


def test_menu_time_is_the_perfect_caller_best_case():
    billing = routing.menu_path("billing")
    assert billing["keys"] == ["1", "3"]
    # Main menu intro, first option, press, submenu intro, three options, press.
    said = [s["text"] for s in billing["steps"] if s["kind"] == "say"]
    assert said[1] == "For billing and payments, press 1."
    assert said[-1] == "For questions about your bill, press 3."
    words = sum(len(text.split()) for text in said)
    expected = (
        words / routing.WORDS_PER_SECOND
        + (len(said) - 2) * routing.PAUSE_SECONDS
        + 2 * routing.KEY_SECONDS
    )
    assert billing["total"] == pytest.approx(expected, abs=0.2)
    # Cancelling is buried under "all other inquiries", so it takes longest.
    totals = {key: routing.menu_path(key)["total"] for key in routing.DEPARTMENTS}
    assert max(totals, key=totals.get) == "cancel"
    assert routing.menu_path("cancel")["keys"] == ["5", "1"]


def test_main_menu_before_routing_has_no_total():
    menu = routing.menu_path(None)
    assert menu["total"] is None and menu["keys"] == []
    assert len(menu["steps"]) == 1 + len(routing.MENU.options)
    with pytest.raises(ValueError):
        routing.menu_path("tv")


def test_scores_normalise_and_ignore_junk():
    result = routing.read_scores({"internet": 50, "visit": 40, "unclear": 10, "tv": 99})
    assert result["internet"] == pytest.approx(0.5) and result["visit"] == pytest.approx(0.4)
    assert sum(result.values()) == pytest.approx(1)
    junk = routing.read_scores({"billing": -5, "mobile": "90", "cancel": True, "moving": math.nan})
    assert junk[routing.UNCLEAR] == 1 and junk["billing"] == 0
    assert routing.read_scores({"billing": 30, "mobile": 30})["billing"] == pytest.approx(0.5)


def test_policy_routes_clarifies_listens_and_stops_asking():
    assert routing.decide(probs(billing=0.9, mobile=0.05), 0)["action"] == "route"
    clarify = routing.decide(probs(internet=0.5, visit=0.4), 0)
    assert clarify["action"] == "clarify"
    assert clarify["between"] == ["internet", "visit"]
    assert routing.decide(probs(unclear=0.8, billing=0.1), 0)["action"] == "listen"
    confirm = routing.decide(probs(mobile=0.5, unclear=0.5), 0)
    assert confirm["action"] == "clarify" and confirm["between"] == ["mobile"]
    assert "confirms" in routing.instruction(confirm)
    forced = routing.decide(probs(internet=0.5, visit=0.4), routing.MAX_CLARIFY)
    assert forced == {
        "action": "route",
        "target": "internet",
        "between": None,
        "confidence": 0.5,
        "forced": True,
    }


def test_transfer_only_goes_where_the_router_says():
    state = routing.initial_state(now=100.0)
    assert "has not chosen" in routing.transfer(state, "wants billing", 101.0)
    routing.record(
        state, "my internet is down and the tech never came", probs(internet=0.5, visit=0.45), 300
    )
    assert state["clarified"] == 1
    assert "has not chosen" in routing.transfer(state, "whatever", 102.0)
    routing.record(state, "the visit", probs(visit=0.92), 280)
    result = routing.transfer(state, "  Technician   missed Tuesday's visit. ", 109.25)
    assert "Technician visits" in result
    assert state["handoff"] == {
        "department": "visit",
        "summary": "Technician missed Tuesday's visit.",
        "confidence": 92,
        "forced": False,
        "seconds": 9.2,
    }
    assert state["history"][-1]["menu"] == routing.menu_path("visit")["total"]
    assert "Already transferred" in routing.transfer(state, "again", 110.0)


def test_new_round_resets_the_race_but_keeps_history():
    state = routing.initial_state(now=0.0)
    routing.record(state, "double charged", probs(billing=0.95), 250)
    routing.transfer(state, "Double charged this month.", 6.0)
    routing.new_round(state, 30.0)
    assert state["round"] == 2 and state["started"] == 30.0
    assert state["routed"] is None and state["handoff"] is None and state["turns"] == []
    assert len(state["history"]) == 1


def test_snapshot_shape_and_router_notes():
    state = routing.initial_state(now=10.0)
    empty = routing.snapshot(state, now=12.5)
    assert empty["elapsedMs"] == 2500 and empty["scores"] == [] and empty["decision"] is None
    assert empty["menu"]["department"] is None and empty["routeAt"] == 75
    decision = routing.record(
        state, "I'm moving and might cancel", probs(moving=0.55, cancel=0.4), 310
    )
    snap = routing.snapshot(state, now=13.0)
    assert snap["decision"] == {
        "action": "clarify",
        "target": None,
        "between": ["moving", "cancel"],
        "confidence": 55,
        "ms": 310,
    }
    assert [s["id"] for s in snap["scores"]][-1] == routing.UNCLEAR
    assert "Ask one short question" in routing.instruction(decision)
    assert "Call transfer" in routing.instruction(routing.decide(probs(cancel=0.8), 0))
    assert "best guess" in routing.instruction(routing.decide(probs(cancel=0.4), 2))
    assert "unavailable" in routing.instruction(routing.record_error(state, "hello"))


def test_transcript_is_bounded_and_labelled():
    state = routing.initial_state()
    for i in range(12):
        routing.hear(state, "caller" if i % 2 else "agent", f"line {i} " + "word " * 80)
    assert len(state["lines"]) == 8
    text = routing.transcript(state)
    assert len(text) <= 1500 and text.splitlines()[-1].startswith("Caller: line 11")


async def test_agent_turn_classifies_publishes_and_injects_the_router_note():
    router = agent.PhoneTreeRouter(SimpleNamespace())
    state = routing.initial_state()
    router._activity = SimpleNamespace(session=SimpleNamespace(userdata=state))
    notes = []
    turn = SimpleNamespace(add_message=lambda **kw: notes.append(kw))

    async def classify(conversation):
        assert conversation == "Caller: I got charged twice"
        return probs(billing=0.93)

    with (
        patch.object(type(router), "session", property(lambda self: self._activity.session)),
        patch.object(router, "classify", classify),
        patch.object(agent, "publish_ui_event") as publish,
    ):
        await router.on_user_turn_completed(
            turn, SimpleNamespace(text_content="I got charged twice")
        )
        assert state["decision"]["action"] == "route"
        assert notes[0]["role"] == "system" and "Billing and payments" in notes[0]["content"]
        assert publish.call_args.args[1] == "Router"
        await router.transfer(SimpleNamespace(userdata=state), "Charged twice this month.")
        assert state["handoff"]["department"] == "billing"

        async def broken(conversation):
            raise TimeoutError

        router._speaking_since = None
        with patch.object(router, "classify", broken):
            await router.on_user_turn_completed(
                turn, SimpleNamespace(text_content="one more thing")
            )
        assert state["round"] == 2 and state["turns"][-1]["action"] == "error"
        assert "unavailable" in notes[-1]["content"]


async def test_classify_requests_strict_scores_and_meters_tokens():
    router = agent.PhoneTreeRouter(SimpleNamespace())
    metered = []
    router.meter_router = lambda prompt, completion: metered.append((prompt, completion))
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=307, completion_tokens=30),
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"visit": 80, "unclear": 20}'))],
    )
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return response

    router._router = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = await router.classify("Caller: when is my technician coming")
    assert result["visit"] == pytest.approx(0.8)
    assert metered == [(307, 30)]
    schema = calls[0]["response_format"]["json_schema"]
    assert schema["strict"] and schema["schema"] is routing.ROUTER_SCHEMA
    assert calls[0]["temperature"] == 0 and calls[0]["store"] is False
