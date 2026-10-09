import random
from datetime import date

import policy

TODAY = date(2026, 10, 9)
ACCOUNT = {
    "brand": "Lumora+",
    "plan": "Premium",
    "price": "22.99",
    "since": "2022",
    "renews": "2026-11-03",
    "profiles": 3,
}


def state():
    return policy.initial_state(dict(ACCOUNT))


def rules(s):
    return [(e["rule"], e["verdict"]) for e in s["log"]]


def test_accounts_vary_and_renew_in_the_future():
    rng = random.Random(3)
    accounts = [policy.new_account(rng, TODAY) for _ in range(20)]
    assert all(date.fromisoformat(a["renews"]) > TODAY for a in accounts)
    assert len({a["renews"] for a in accounts}) > 5


def test_code_hears_cancel_not_negations():
    assert policy.wants_cancel("I want to cancel my membership")
    assert policy.wants_cancel("please end my subscription")
    assert not policy.wants_cancel("don't cancel it, I just want a new password")
    assert not policy.wants_cancel("what does premium include")


def test_price_reason_gets_one_offer_with_a_spoken_disclosure():
    s = state()
    assert not policy.observe_caller(s, "Hi, I'd like to cancel")
    assert s["intent_turn"] == 1
    assert "make_offer" in policy.note_reason(s, "price")
    assert policy.make_offer(s).startswith("approved: half price")
    assert policy.make_offer(s).startswith("blocked")
    script = policy.take_script(s)
    assert "say cancel at any time" in script
    assert policy.take_script(s) == ""
    assert ("OFFER.MATCH", "block") in rules(s)
    assert ("OFFER.DISCLOSE", "pass") in rules(s)


def test_no_offer_for_switching_and_no_offer_before_a_cancel_request():
    s = state()
    assert policy.make_offer(s).startswith("blocked: the caller has not asked")
    policy.observe_caller(s, "cancel please")
    assert "No offer applies" in policy.note_reason(s, "switching")
    assert policy.make_offer(s).startswith("blocked: no offer fits")
    assert s["offers"] == []


def test_declining_the_offer_makes_code_cancel():
    s = state()
    policy.observe_caller(s, "cancel my subscription")
    policy.note_reason(s, "not_watching")
    policy.make_offer(s)
    assert policy.observe_caller(s, "No thanks, just cancel it")
    policy.cancel(s, by="policy")
    assert s["outcome"]["kind"] == "cancelled" and s["outcome"]["by"] == "policy"
    assert s["offers"][0]["status"] == "declined"
    script = policy.take_script(s)
    assert "cancelled" in script and "November 3rd" in script
    assert policy.spell(s["outcome"]["ref"]) in script
    assert policy.snapshot(s)["clock"]["state"] == "enforced"


def test_deadline_cancels_after_three_turns_of_stalling():
    s = state()
    assert not policy.observe_caller(s, "I want to cancel")
    assert not policy.observe_caller(s, "hmm")
    assert policy.turns_left(s) == 2
    assert not policy.observe_caller(s, "well")
    assert policy.observe_caller(s, "let me think")
    assert ("CANCEL.DEADLINE", "enforce") in rules(s)


def test_a_late_yes_still_wins_over_the_clock():
    s = state()
    policy.observe_caller(s, "cancel")
    policy.observe_caller(s, "it's too expensive")
    policy.note_reason(s, "price")
    policy.make_offer(s)
    policy.observe_caller(s, "how long does it last")
    assert not policy.observe_caller(s, "yes, I'll take it")
    assert policy.accept_offer(s, s.get("last_caller", "yes, I'll take it")).startswith("applied")
    assert s["outcome"]["kind"] == "retained"
    assert policy.cancel(s).startswith("blocked")


def test_acceptance_needs_a_clear_yes():
    s = state()
    policy.observe_caller(s, "cancel")
    policy.note_reason(s, "price")
    policy.make_offer(s)
    assert policy.accept_offer(s, "hmm maybe").startswith("blocked")
    assert policy.accept_offer(s, "no, cancel").startswith("blocked")
    assert ("OFFER.CONSENT", "block") in rules(s)
    assert s["outcome"] is None


def test_model_cannot_cancel_unasked_and_cancel_is_idempotent():
    s = state()
    assert policy.cancel(s).startswith("blocked")
    policy.observe_caller(s, "I want to cancel")
    first = policy.cancel(s)
    ref = s["outcome"]["ref"]
    assert first.startswith("cancelled")
    assert policy.cancel(s).startswith("already")
    assert s["outcome"]["ref"] == ref
    assert not policy.observe_caller(s, "cancel")


def test_screen_drops_unapproved_deals_and_pressure():
    s = state()
    policy.observe_caller(s, "cancel")
    assert policy.screen(s, "I can give you 30% off for a year. ") == ""
    assert policy.screen(s, "Are you sure? ") == ""
    assert policy.screen(s, "Why are you leaving? ") == "Why are you leaving? "
    policy.note_reason(s, "price")
    policy.make_offer(s)
    assert policy.screen(s, "That is half price for three months. ")
    assert policy.screen(s, "You'll lose your watchlist. ") == ""
    assert [e["rule"] for e in s["log"]].count("SPEECH.SCREEN") == 3


def test_sentences_split_on_boundaries():
    assert policy.split_sentences("Sure. I can") == (["Sure. "], "I can")
    assert policy.split_sentences("no end") == ([], "no end")


def test_refs_avoid_sound_alikes_and_snapshot_is_bounded():
    s = state()
    policy.observe_caller(s, "cancel")
    policy.note_reason(s, "price")
    for _ in range(12):
        policy.make_offer(s)
    policy.cancel(s)
    ref = s["outcome"]["ref"]
    assert ref.startswith("CX-") and set(ref[3:].replace("-", "")) <= set("3479ACFHKMRX")
    snap = policy.snapshot(s)
    assert len(snap["log"]) == policy.LOG_LIMIT
    assert snap["clock"] == {"limit": 3, "used": 0, "state": "cancelled"}
