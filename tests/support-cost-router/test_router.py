from decimal import Decimal

import pytest
import router

TEXTS, LABELS = router.index_texts()
# One axis per indexed phrase: a query on an axis matches that phrase exactly.
VECTORS = [[1.0 if i == n else 0.0 for i in range(len(TEXTS))] for n in range(len(TEXTS))]
INDEX = {"vectors": VECTORS, "labels": LABELS}


def near(label, weight=1.0, rest=("class", "chat")):
    vector = [0.0] * len(TEXTS)
    vector[LABELS.index(label)] = weight
    # The rest of the weight on a second phrase lowers the cosine below 1.
    vector[LABELS.index(rest)] += 1.0 - weight
    return vector


def test_cache_hit_speaks_the_stored_answer():
    r = router.route("is roaming free in mexico", near(("faq", "roaming")), INDEX, [])
    assert r.tier == "cache"
    assert r.answer == router.faq_answer("roaming")
    assert r.model == ""


def test_near_miss_goes_to_a_model_and_is_learned():
    r = router.route(
        "what about roaming", near(("faq", "roaming"), 0.45, ("class", "general")), INDEX, []
    )
    assert r.tier == "mini"
    assert r.learn
    learned = [{"key": "learned-1", "vector": r.vector, "answer": "Yes, it is."}]
    again = router.route("roaming again", r.vector, INDEX, learned)
    assert again.tier == "cache"
    assert again.answer == "Yes, it is."


@pytest.mark.parametrize(
    ("label", "tier"),
    [(("class", "chat"), "nano"), (("class", "account"), "mini"), (("class", "escalate"), "large")],
)
def test_each_class_routes_to_its_tier(label, tier):
    assert router.route("something", near(label), INDEX, []).tier == tier


def test_escalation_words_win_over_everything():
    r = router.route("I want a refund", near(("faq", "roaming")), INDEX, [])
    assert r.tier == "large"
    assert "'refund'" in r.reason


def test_disputes_stay_on_the_big_model_until_resolved():
    state = router.initial_state()
    first = router.route("you overcharged me", None, INDEX, [], state["dispute_turns"])
    router.track_dispute(state, first)
    follow = router.route("okay", near(("class", "chat")), INDEX, [], state["dispute_turns"])
    assert follow.tier == "large"
    router.track_dispute(state, follow)
    assert router.credit(state, "device protection").startswith("credited")
    after = router.route("thanks", near(("class", "chat")), INDEX, [], state["dispute_turns"])
    assert after.tier == "nano"


def test_short_account_turns_are_not_small_talk():
    r = router.route("my bill?", near(("class", "chat")), INDEX, [])
    assert r.tier == "mini"


def test_router_offline_falls_back_to_rules_never_the_cache():
    assert router.route("thanks", None, INDEX, []).tier == "nano"
    assert router.route("is roaming free in mexico", None, INDEX, []).tier == "mini"


def test_credit_only_true_duplicates_and_only_once():
    state = router.initial_state()
    assert router.credit(state, "travel day pass").startswith("rejected")
    assert router.credit(state, "plan").startswith("rejected")
    assert router.credit(state, "Device protection").startswith("credited")
    assert router.credit(state, "device protection").startswith("already credited")
    assert state["credited"] == ["Device protection"]


def test_prices_follow_the_published_rates():
    # 1M prompt tokens with half cached, plus 1M completion tokens, on gpt-4.1.
    cost = router.llm_cost("gpt-4.1", 1_000_000, 1_000_000, 500_000)
    assert cost == Decimal("1.00") + Decimal("0.25") + Decimal("8.00")
    assert router.embed_cost(1_000_000) == Decimal("0.02")


def test_ledger_compares_each_turn_with_the_big_model():
    ledger = router.Ledger()
    hit = ledger.open("roaming?", router.Route("cache", "hit"), router_tokens=10)
    router.Ledger.add_skipped(hit, "x" * 4000, "y" * 400)
    small = ledger.open("thanks", router.Route("nano", "chat"), router_tokens=5)
    router.Ledger.add_usage(small, "gpt-4.1-nano", 1000, 20, 0)
    totals = ledger.totals()
    assert hit.estimated and hit.actual == 0
    assert small.baseline == router.llm_cost("gpt-4.1", 1000, 20)
    assert totals["actual"] == router.embed_cost(15) + router.llm_cost("gpt-4.1-nano", 1000, 20)
    assert totals["saved"] > Decimal("0.9")
    assert totals["mix"] == {"cache": 1, "nano": 1, "mini": 0, "large": 0}


def test_snapshot_is_plain_json():
    ledger = router.Ledger()
    turn = ledger.open("hello", router.Route("nano", "chat"))
    router.Ledger.add_usage(turn, "gpt-4.1-nano", 500, 10, 0)
    snap = router.snapshot(ledger, [])
    assert snap["turns"][0]["model"] == "gpt-4.1-nano"
    assert isinstance(snap["actualUsd"], float)
    assert snap["monthlyBaselineUsd"] >= snap["monthlyActualUsd"]
    assert len(router.snapshot(router.Ledger(), [])["turns"]) == 0
