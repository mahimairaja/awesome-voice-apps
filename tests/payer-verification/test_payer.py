import payer
import pytest


def walk(line: payer.PayerLine) -> list[str]:
    pressed = []
    while not line.on_hold:
        keys = line.expected()
        ok, reply = line.press(keys)
        assert ok and reply == ""
        pressed.append(keys)
    return pressed


def test_npi_check_digit():
    assert payer.luhn_npi(payer.NPI)
    assert not payer.luhn_npi("1234567890")
    assert not payer.luhn_npi("12345")


def test_menu_order_changes_between_calls():
    prompts = {payer.PayerLine(seed).prompt() for seed in range(12)}
    assert len(prompts) > 1


def test_known_route_reaches_hold():
    line = payer.PayerLine(7)
    pressed = walk(line)
    assert pressed[1] == payer.NPI + "#"
    assert len(pressed) == 3


def test_wrong_keys_retry_then_fall_through_to_a_person():
    line = payer.PayerLine(3)
    ok, reply = line.press("9")
    assert not ok and "not a valid option" in reply
    assert line.node == "welcome"
    ok, reply = line.press("9")
    assert not ok and "Transferring" in reply
    assert line.on_hold


def test_valid_but_wrong_department_is_refused():
    line = payer.PayerLine(5)
    members = line.menus["welcome"].key_for("members")
    ok, reply = line.press(members)
    assert not ok and "closed" in reply
    assert line.node == "welcome"


def test_bad_npi_is_rejected():
    line = payer.PayerLine(1)
    line.press(line.expected())
    ok, reply = line.press("1234567890#")
    assert not ok and "N P I" in reply
    ok, _ = line.press(payer.NPI)  # no pound key
    assert not ok


def test_clean_keys_strips_speech():
    assert payer.clean_keys("press 2, then #") == "2#"


def test_hold_signals():
    signals = payer.hold_signals("Your call is important to us, please hold.", [])
    assert signals["scripted"] and payer.fallback_verdict(signals) == "recording"
    person = payer.hold_signals("Provider services, this is Dana. Can I get your NPI?", [])
    assert person["cues"] and payer.fallback_verdict(person) == "human"
    loop = payer.hold_signals("Hello?", ["hello"])
    assert loop["repeated"]


def test_record_normalises_and_tracks_corrections():
    record = payer.new_record()
    note = payer.apply(
        record,
        {"coverage": "active", "copay": 30, "coinsurance": 0.2},
        "she's active, copay's thirty, twenty percent",
    )
    assert "Recorded" in note and "Still needed" in note
    assert record["coinsurance"]["display"] == "20%"
    assert record["copay"]["source"].startswith("she's active")
    payer.apply(record, {"copay": 50}, "sorry, specialist is fifty")
    assert record["copay"]["status"] == "corrected"
    assert record["copay"]["prev"] == "$30"
    assert record["copay"]["display"] == "$50"


def test_met_above_deductible_is_a_conflict():
    record = payer.new_record()
    note = payer.apply(record, {"deductible": 500, "deductible_met": 800}, "")
    assert "Problems" in note
    assert record["deductible_met"]["status"] == "conflict"
    assert "deductible_met" in payer.missing(record)
    payer.apply(record, {"deductible": 1500}, "")
    assert record["deductible_met"]["status"] != "conflict"
    assert payer.remaining(record) == "$700"


@pytest.mark.parametrize(
    ("field", "raw"),
    [
        ("copay", -5),
        ("copay", 9000),
        ("coinsurance", 140),
        ("reference", "x"),
        ("coverage", "maybe"),
    ],
)
def test_bad_values_are_refused(field, raw):
    record = payer.new_record()
    note = payer.apply(record, {field: raw}, "")
    assert "Problems" in note
    assert record[field]["status"] == "empty"


def test_complete_record_and_inactive_plans():
    record = payer.new_record()
    payer.apply(
        record,
        {
            "coverage": "active",
            "copay": 50,
            "deductible": 1500,
            "deductible_met": 420,
            "coinsurance": 20,
            "prior_auth": True,
            "reference": "hb-42 k 118",
        },
        "",
    )
    assert payer.missing(record) == []
    result = payer.summary(record)
    assert result["reference"] == "HB42K118"
    assert result["prior_auth"] == {"cpt": "70551", "required": True}
    inactive = payer.new_record()
    payer.apply(inactive, {"coverage": "inactive"}, "")
    assert payer.missing(inactive) == ["reference"]


def test_rep_screen_varies_and_is_parseable():
    screens = [payer.rep_screen(seed) for seed in range(8)]
    assert len({s["reference"] for s in screens}) > 1
    assert all(
        payer.normalise("reference", s["reference"]) == (s["reference"],) * 2 for s in screens
    )


def test_tones_are_whole_frames():
    for pcm in (payer.dtmf_audio("12#"), payer.ringback_audio(), payer.hold_music(2.0)):
        chunks = payer.frames(pcm)
        assert chunks and all(len(c) == payer.FRAME for c in chunks)
    assert len(payer.dtmf_audio("1")) == int(payer.SAMPLE_RATE * 0.16)
