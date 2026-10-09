import random
from datetime import date

import protocol

TODAY = date(2026, 10, 9)
CLEAR = {
    "breathing": {"chest_pain_or_short_of_breath": False},
    "calf": {"calf_pain_or_swelling": False},
    "temperature": {"reading": 99.1, "unit": "F", "chills_or_sweats": False},
    "wound": {"redness_spreading": False, "drainage": "none", "edges_opening": False},
    "pain": {"score": 4, "medication_helping": True},
}


def state():
    return protocol.initial_state(protocol.new_patient(random.Random(1), TODAY))


def answer_all(s, **overrides):
    results = []
    for step in protocol.STEP_IDS:
        results.append(protocol.record(s, step, overrides.get(step, CLEAR[step]), "words"))
    return results


def test_patient_is_on_day_three():
    p = protocol.new_patient(random.Random(3), TODAY)
    assert p["surgery_date"] == "2026-10-06"
    assert p["next_check_in"] == "2026-10-13"
    assert p["procedure"].startswith("Total knee replacement")


def test_protocol_drives_the_next_question():
    s = state()
    assert protocol.current_step(s) == "breathing"
    result = protocol.record(s, "breathing", CLEAR["breathing"], "no, breathing fine")
    assert protocol.STEP_BY_ID["calf"]["ask"] in result
    # Volunteered out of order: recorded, and the protocol still asks the calf next.
    protocol.record(s, "pain", CLEAR["pain"], "about a four")
    assert protocol.current_step(s) == "calf"


def test_all_clear_is_routine():
    s = state()
    results = answer_all(s)
    assert "finish_check_in" in results[-1]
    said = protocol.finish(s)
    assert said.startswith("Outcome ROUTINE") and "2026-10-13" in said
    assert s["outcome"]["tier"] == "routine" and s["handoff"] is None
    assert s["outcome"]["ref"].startswith("CI-")


def test_chest_pain_stops_the_call():
    s = state()
    result = protocol.record(s, "breathing", {"chest_pain_or_short_of_breath": True}, "a bit")
    assert result.startswith("EMERGENCY") and "911" in result
    assert s["outcome"]["tier"] == "emergency" and s["outcome"]["rules"] == ["E1"]
    assert protocol.current_step(s) is None
    # Nothing else can be recorded and finish cannot turn it into a routine call.
    assert "911" in protocol.record(s, "calf", CLEAR["calf"], "")
    assert s["answers"]["calf"] is None
    assert "emergency" in protocol.finish(s)
    snap = protocol.snapshot(s)
    assert [x["status"] for x in snap["steps"]] == [
        "emergency",
        "skipped",
        "skipped",
        "skipped",
        "skipped",
    ]
    assert snap["handoff"]["recommendation"].startswith("Patient told to call 911")


def test_fever_goes_to_the_nurse_with_sbar():
    s = state()
    hot = {"reading": 38.9, "unit": "unknown", "chills_or_sweats": False}
    results = answer_all(s, temperature=hot)
    assert "Rule N2 fired" in results[2]
    said = protocol.finish(s)
    assert said.startswith("Outcome NURSE") and "fever after surgery" in said
    note = s["handoff"]
    assert "rule N2" in note["situation"]
    assert "Temperature: 102°F (38.9°C)" in note["assessment"]
    assert len(note["assessment"]) == 5
    assert s["outcome"]["ref"].startswith("NL-")


def test_thresholds():
    cases = [
        ("temperature", {"reading": 101.4, "unit": "F", "chills_or_sweats": False}, []),
        ("temperature", {"reading": 101.5, "unit": "F", "chills_or_sweats": False}, ["N2"]),
        ("temperature", {"reading": None, "unit": "unknown", "chills_or_sweats": True}, ["N3"]),
        ("temperature", {"reading": None, "unit": "unknown", "chills_or_sweats": False}, []),
        ("pain", {"score": 8, "medication_helping": True}, []),
        ("pain", {"score": 8, "medication_helping": False}, ["N7"]),
        ("pain", {"score": 7, "medication_helping": False}, []),
        (
            "wound",
            {"redness_spreading": True, "drainage": "clear", "edges_opening": True},
            [
                "N5",
                "N6",
            ],
        ),
        (
            "wound",
            {"redness_spreading": False, "drainage": "cloudy_or_pus", "edges_opening": False},
            ["N4"],
        ),
        ("wound", {"redness_spreading": False, "drainage": "spotting", "edges_opening": False}, []),
        ("calf", {"calf_pain_or_swelling": True}, ["N1"]),
    ]
    for step, value, rules in cases:
        s = state()
        protocol.record(s, step, value, "")
        assert s["answers"][step]["rules"] == rules, (step, value)


def test_impossible_values_are_asked_again():
    s = state()
    result = protocol.record(
        s, "temperature", {"reading": 1015, "unit": "F", "chills_or_sweats": False}, ""
    )
    assert result.startswith("rejected") and s["answers"]["temperature"] is None
    assert protocol.record(s, "pain", {"score": 12, "medication_helping": True}, "").startswith(
        "rejected"
    )


def test_a_correction_never_clears_a_fired_rule():
    s = state()
    protocol.record(
        s, "temperature", {"reading": 102, "unit": "F", "chills_or_sweats": False}, "one oh two"
    )
    result = protocol.record(
        s,
        "temperature",
        {"reading": 99.5, "unit": "F", "chills_or_sweats": False},
        "no wait, it was ninety nine five, it's nothing",
    )
    assert "stays on the record" in result
    answer = s["answers"]["temperature"]
    assert answer["summary"].startswith("99.5°F") and answer["was"].startswith("102°F")
    assert answer["rules"] == ["N2"]
    assert protocol.tier(s) == "nurse"
    fired = protocol.fired_rules(s)
    assert fired[0]["observed"].startswith("102°F")


def test_finish_and_start_over_are_gated():
    s = state()
    protocol.record(s, "breathing", CLEAR["breathing"], "")
    refused = protocol.finish(s)
    assert refused.startswith("refused") and "calf" in refused and s["outcome"] is None
    assert protocol.start_over(s).startswith("refused")
    answer_all(s)
    protocol.finish(s)
    patient = s["patient"]
    assert protocol.start_over(s).startswith("Reset")
    assert s["outcome"] is None and s["patient"] == patient
    assert protocol.current_step(s) == "breathing"


def test_snapshot_shape():
    s = state()
    protocol.record(s, "breathing", CLEAR["breathing"], "  nope,   all good  ")
    protocol.record(s, "calf", {"calf_pain_or_swelling": True}, "left calf is sore")
    snap = protocol.snapshot(s)
    assert snap["protocol"]["id"] == protocol.PROTOCOL
    assert [x["status"] for x in snap["steps"]] == [
        "clear",
        "flag",
        "current",
        "pending",
        "pending",
    ]
    assert snap["steps"][0]["quote"] == "nope, all good"
    assert snap["tier"] == "nurse" and snap["outcome"] is None
    fired = [r for r in snap["rules"] if r["fired"]]
    assert [(r["id"], r["observed"]) for r in fired] == [("N1", "Calf pain or swelling")]
    assert len(snap["rules"]) == len(protocol.RULES)


def test_handoff_shows_the_earlier_answer():
    s = state()
    protocol.record(s, "temperature", {"reading": 102, "unit": "F", "chills_or_sweats": False}, "")
    protocol.record(s, "temperature", {"reading": 99, "unit": "F", "chills_or_sweats": False}, "")
    for step in ("breathing", "calf", "wound", "pain"):
        protocol.record(s, step, CLEAR[step], "")
    assert protocol.finish(s).startswith("Outcome NURSE")
    assert "Temperature: 99°F (37.2°C), earlier 102°F (38.9°C)" in s["handoff"]["assessment"]
