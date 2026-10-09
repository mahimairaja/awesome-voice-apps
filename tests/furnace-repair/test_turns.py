from turns import TurnTimeline


def timeline():
    events = []
    return TurnTimeline(events.append, timer_silence=0.5, clock=lambda: 0.0), events


def test_marks_where_a_silence_timer_would_cut_and_the_detector_held():
    t, events = timeline()
    t.user_state("speaking", at=10.0)
    t.transcript("it's 42", final=True)
    t.prediction(0.08, 0.3)  # detector answers during the silence
    t.user_state("listening", at=11.2)  # VAD silence reached: the timer cuts here
    t.user_state("speaking", at=11.9)  # ... uh ... Maple
    t.transcript("Maple Street", final=True)
    t.prediction(0.92, 0.3)
    t.user_state("listening", at=13.0)
    t.commit("it's 42 Maple Street", at=13.1)

    turn = events[-1]["turn"]
    assert turn["segments"] == [[0, 700], [1900, 2500]]
    first, last = turn["pauses"]
    assert first == {
        "cut": 1200,
        "quiet": 700,
        "p": 0.08,
        "th": 0.3,
        "held": True,
        "heard": "it's 42",
    }
    assert last["held"] is False and last["p"] == 0.92
    assert turn["commit"] == 3100 and turn["said"] == "it's 42 Maple Street"
    assert events[-1]["totals"] == {"turns": 1, "saved": 1, "waited_ms": 100}


def test_late_prediction_attaches_to_the_open_pause_only():
    t, events = timeline()
    t.user_state("speaking", at=0.0)
    t.user_state("listening", at=1.0)
    t.prediction(0.2, None)
    assert events[-1]["turn"]["pauses"][0]["p"] == 0.2
    t.user_state("speaking", at=1.5)
    t.prediction(0.9, 0.3)  # belongs to no pause yet
    assert events[-1]["turn"]["pauses"][0]["p"] == 0.2


def test_new_turn_starts_after_commit_and_text_is_bounded():
    t, events = timeline()
    t.user_state("speaking", at=0.0)
    t.commit("x" * 500, at=0.4)
    assert len(events[-1]["turn"]["said"]) == 160
    assert events[-1]["turn"]["segments"] == [[0, 400]]
    t.user_state("speaking", at=5.0)
    assert events[-1]["turn"]["n"] == 2 and events[-1]["turn"]["pauses"] == []
    for i in range(30):
        t.user_state("listening", at=6.0 + i)
        t.user_state("speaking", at=6.5 + i)
    assert len(events[-1]["turn"]["pauses"]) == 12
    assert len(events[-1]["turn"]["segments"]) == 12


def test_listening_without_a_turn_is_ignored():
    t, events = timeline()
    t.user_state("listening", at=1.0)
    t.commit("hello", at=2.0)
    assert events == []
    assert t.snapshot()["turn"] is None
