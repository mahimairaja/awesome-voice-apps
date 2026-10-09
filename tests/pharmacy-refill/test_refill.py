import random
from datetime import date

import refill

TODAY = date(2026, 10, 9)
PROFILE = {
    "patient": "Jordan Ellis",
    "drug": "metformin",
    "strength": "500 mg",
    "rx_number": "4471-B",
    "date_of_birth": "1982-03-14",
    "postal_code": "M5V 2T6",
    "refills_left": 2,
}


def state():
    return refill.initial_state(dict(PROFILE))


def test_profiles_are_valid_and_vary():
    rng = random.Random(7)
    profiles = [refill.new_profile(rng, TODAY) for _ in range(20)]
    for p in profiles:
        assert refill.RX_RE.match(p["rx_number"].replace("-", ""))
        assert refill.POSTAL_RE.match(p["postal_code"].replace(" ", ""))
        assert p["drug"] in refill.FORMULARY
        assert refill.normalize_dob(p["date_of_birth"], TODAY)[0]
    assert len({p["rx_number"] for p in profiles}) > 10


def test_rx_number_normalizes_spoken_forms():
    for heard in ("4471-B", "4471 b", "four four seven one B as in Bravo", "4 4 7 1 bee"):
        assert refill.normalize_rx(heard) == (True, "4471-B"), heard
    assert not refill.normalize_rx("447B")[0]


def test_postal_code_follows_canada_post_rules():
    assert refill.normalize_postal("m5v 2t6") == (True, "M5V 2T6")
    assert refill.normalize_postal("M as in Mike five V two T six") == (True, "M5V 2T6")
    # Canada Post never uses D, F, I, O, Q or U.
    assert not refill.normalize_postal("D5V 2T6")[0]
    assert not refill.normalize_postal("90210")[0]


def test_drug_sound_alikes_are_flagged_not_guessed():
    ok, drug, note = refill.normalize_drug("metformin")
    assert ok and drug == "metformin" and "metoprolol" in note
    ok, drug, _ = refill.normalize_drug("Metformine")
    assert ok and drug == "metformin"
    ok, reason, _ = refill.normalize_drug("aspirin")
    assert not ok and "formulary" in reason


def test_confirm_checks_the_file_without_revealing_it():
    s = state()
    assert "4471-D" in refill.capture(s, "rx_number", "4471-D", TODAY)
    assert s["fields"]["rx_number"]["status"] == "heard"
    assert s["fields"]["rx_number"]["score"] < 100
    result = refill.confirm(s, "rx_number")
    assert result.startswith("mismatch") and "4471-B" not in result
    refill.capture(s, "rx_number", "4471-B", TODAY)
    assert refill.confirm(s, "rx_number") == "confirmed Rx number"
    assert s["fields"]["rx_number"]["score"] == 100


def test_sound_alike_drug_is_a_mismatch():
    s = state()
    refill.capture(s, "drug", "metoprolol", TODAY)
    assert refill.confirm(s, "drug").startswith("mismatch")


def test_cannot_confirm_before_capture_or_refill_before_all_confirmed():
    s = state()
    assert "capture it first" in refill.confirm(s, "postal_code")
    assert "unconfirmed" in refill.place_refill(s, TODAY)
    for field, heard in [
        ("drug", "metformin"),
        ("rx_number", "4471B"),
        ("date_of_birth", "1982-03-14"),
        ("postal_code", "m5v2t6"),
        ("pickup_store", "the Danforth one"),
    ]:
        refill.capture(s, field, heard, TODAY)
        assert refill.confirm(s, field).startswith("confirmed"), field
    first = refill.place_refill(s, TODAY)
    assert "Danforth Avenue" in first and s["ref"].startswith("RF-1009-")
    assert refill.place_refill(s, TODAY) == first


def test_dates_reject_future_and_bad_formats():
    assert not refill.normalize_dob("2030-01-01", TODAY)[0]
    assert not refill.normalize_dob("March 14", TODAY)[0]


def test_codes_are_spoken_character_by_character():
    text, pairs = refill.speakable("I have Rx 4471-B, postal code M5V 2T6, born 1982-03-14.")
    assert "four, four, seven, one, B as in Bravo" in text
    assert "M as in Mike, five, V as in Victor. two, T as in Tango, six" in text
    assert "March 14th, 1982" in text
    assert [w for w, _ in pairs] == ["1982-03-14", "M5V 2T6", "4471-B"]


def test_stream_split_never_cuts_a_code():
    assert refill.split_ready("postal code M5V") == ("postal code ", "M5V")
    assert refill.split_ready("code M5V 2T") == ("code ", "M5V 2T")
    assert refill.split_ready("Rx 4471-B is") == ("Rx 4471-B ", "is")
    assert refill.split_ready("nospace") == ("", "nospace")


def test_redaction_masks_pii_before_logging():
    cases = {
        "I was born March 14th, 1982 thanks": "DOB",
        "born on march fourteenth nineteen eighty two": "DOB",
        "the 14th of March 1982": "DOB",
        "my date of birth is 1982-03-14": "DOB",
        "call me at (416) 555-0199": "PHONE",
        "four one six five five five zero one nine nine": "PHONE",
        "postal code M5V 2T6": "POSTAL",
        "it's m five v two t six": "POSTAL",
        "email jo@example.com": "EMAIL",
    }
    for line, tag in cases.items():
        clean, tags = refill.redact(line)
        assert tag in tags, (line, clean)
        assert not any(c.isdigit() for c in clean), clean
    clean, tags = refill.redact("Rx 4471-B for metformin, may I pick it up")
    assert clean == "Rx 4471-B for metformin, may I pick it up" and tags == []


def test_snapshot_shows_tall_man_names_and_caps_lists():
    s = state()
    refill.capture(s, "drug", "metformin", TODAY)
    s["log"] = [{"who": "caller", "text": str(i), "tags": []} for i in range(10)]
    snap = refill.snapshot(s)
    assert snap["label"]["drug"] == "metFORMIN"
    assert snap["fields"][0]["value"] == "metFORMIN"
    assert len(snap["log"]) == 6
