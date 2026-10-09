import random

import coach


def heard(text: str, conf: float = 0.97, low: dict | None = None) -> list[tuple[str, float]]:
    low = low or {}
    return [(w, low.get(coach.key(w), conf)) for w in text.split()]


def started(text: str, language: str = "en", level: int | None = None) -> dict:
    state = coach.initial_state()
    coach.choose_language(state, language)
    found, phrase = next(
        (n, p) for n, pool in enumerate(coach.PHRASES[language]) for p in pool if p["text"] == text
    )
    state["level"] = found if level is None else level
    state["phrase"] = {**phrase, "words": coach.tokenize(phrase["text"])}
    return state


def test_alignment_scores_each_target_word():
    words = coach.align(
        coach.tokenize("La grenouille rouge saute sur le mur."),
        [
            ("la", 0.99),
            ("grenouille", 0.91),
            ("rush", 0.8),
            ("saute", 0.95),
            ("sur", 0.6),
            ("le", 0.99),
        ],
    )
    by = {w["word"]: w for w in words}
    assert by["grenouille"]["status"] == "clear"
    assert (by["rouge"]["status"], by["rouge"]["heard"], by["rouge"]["score"]) == (
        "misheard",
        "rush",
        0,
    )
    assert by["sur"]["status"] == "weak"
    assert (by["mur"]["status"], by["mur"]["score"]) == ("missed", 0)


def test_spelling_accents_and_punctuation_do_not_cost_points():
    words = coach.align(
        coach.tokenize("Les chaussettes de l'archiduchesse sont sèches."),
        heard("les chaussettes de l’archiduchesse sont seches", 0.93),
    )
    assert all(w["status"] == "clear" for w in words)


def test_talk_is_not_scored_as_a_read():
    state = started("I think it is Thursday.", level=1)
    assert coach.score_turn(state, heard("can you say that again please")) is None
    assert coach.score_turn(state, []) is None
    assert state["mode"] == "phrase" and state["attempts"] == []


def test_clean_first_read_passes_and_moves_up():
    state = started(text="The weather this month is rather cold.")
    result = coach.score_turn(state, heard("the weather this month is rather cold", 0.96))
    assert (result["outcome"], result["change"]) == ("pass", "up")
    assert state["level"] == 2 and state["mode"] == "phrase"
    assert result["next"] == state["phrase"]["text"]
    assert state["history"][-1]["first"] == 96
    assert "moved up" in coach.brief(state, result)


def test_weak_words_are_drilled_then_the_line_is_reread():
    state = started("I think it is Thursday.", level=1)
    first = coach.score_turn(state, heard("I sink it is thursday", 0.95, low={"thursday": 0.52}))
    assert first["outcome"] == "coach"
    assert [t["word"] for t in first["targets"]] == ["think", "Thursday"]
    assert state["targets"] == [1, 4]
    line = coach.brief(state, first)
    assert "'think' (sounded like 'sink')" in line and "say just: think, Thursday" in line

    again = coach.score_turn(state, heard("think thursday", 0.9, low={"thursday": 0.6}))
    assert again["outcome"] == "drill_again" and state["targets"] == [4]
    fixed = coach.score_turn(state, heard("thursday", 0.88))
    assert fixed["outcome"] == "drill_pass" and state["mode"] == "retry"
    assert fixed["targets"][0]["before"] == 52

    done = coach.score_turn(state, heard("I think it is Thursday", 0.94))
    assert done["outcome"] == "retry_done" and done["change"] == "same"
    assert {g["word"]: (g["before"], g["score"]) for g in done["gains"]} == {
        "think": (0, 94),
        "Thursday": (52, 94),
    }
    assert state["mode"] == "phrase" and state["level"] == 1
    assert "first read was" in coach.brief(state, done)


def test_drill_gives_up_after_two_tries():
    state = started(text="Tu as vu la rue?", language="fr", level=0)
    coach.score_turn(state, heard("tu as vous la rue", 0.95))
    assert coach.score_turn(state, heard("vous", 0.9))["outcome"] == "drill_again"
    assert coach.score_turn(state, heard("vous", 0.9))["outcome"] == "drill_move_on"
    assert state["mode"] == "retry"


def test_poor_first_read_moves_down_and_level_is_bounded():
    state = started(text="Red lorry, yellow lorry, rolling rapidly.", level=2)
    coach.score_turn(state, heard("red lorry yellow lolly rolling rabbit", 0.5))
    coach.score_turn(state, heard("lorry rapidly", 0.4))
    coach.score_turn(state, heard("lorry rapidly", 0.4))
    done = coach.score_turn(state, heard("red lorry yellow lorry rolling rapidly", 0.6))
    assert done["change"] == "down" and state["level"] == 1
    low = started(text="Very well, we will wait.", level=0)
    coach.score_turn(low, heard("berry well we will wait", 0.4))
    assert low["level"] == 0


def test_state_is_per_call_and_phrases_do_not_repeat_until_exhausted():
    first, second = coach.initial_state(), coach.initial_state()
    coach.choose_language(first, "fr")
    assert second["language"] is None and second["used"] == []
    rng = random.Random(1)
    seen = {coach.next_phrase(first, rng)["text"] for _ in range(2)}
    assert len(seen | {first["used"][0]}) == 3
    coach.next_phrase(first, rng)
    assert first["phrase"]["text"] in {p["text"] for p in coach.PHRASES["fr"][1]}


def test_skip_and_snapshot():
    state = started(text="Il y a un bon vin blanc.", language="fr")
    coach.score_turn(state, heard("il y a un bon vent blanc", 0.95))
    view = coach.snapshot(state)
    assert view["mode"] == "drill" and view["targets"] == [5]
    assert view["attempts"][0]["words"][5] == {
        "word": "vin",
        "index": 5,
        "score": 0,
        "status": "misheard",
        "heard": "vent",
    }
    coach.skip(state)
    assert state["history"][-1]["change"] == "skipped" or state["history"][-1]["first"] is not None
    assert coach.snapshot(state)["mode"] == "phrase"
    try:
        coach.choose_language(state, "de")
    except ValueError:
        pass
    else:
        raise AssertionError("German should be refused")
