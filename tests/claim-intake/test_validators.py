from datetime import date

from agent import (
    FIELDS,
    VALIDATORS,
    normalize_yesno,
    validate_date_of_loss,
    validate_policy_number,
)


def test_policy_number_normalizes_and_accepts():
    ok, value = validate_policy_number("ab 123456")
    assert ok and value == "AB123456"


def test_policy_number_rejects_bad_format():
    ok, reason = validate_policy_number("12345")
    assert not ok and "two letters" in reason


def test_date_accepts_past_iso():
    ok, value = validate_date_of_loss("2026-03-03", today=date(2026, 7, 8))
    assert ok and value == "2026-03-03"


def test_date_rejects_future():
    ok, reason = validate_date_of_loss("2099-01-01", today=date(2026, 7, 8))
    assert not ok and "future" in reason


def test_date_rejects_unparseable():
    ok, reason = validate_date_of_loss("last tuesday", today=date(2026, 7, 8))
    assert not ok and "year-month-day" in reason


def test_yesno_normalizes_loosely():
    assert normalize_yesno("Yeah") == (True, "yes")
    assert normalize_yesno("nope.") == (True, "no")


def test_yesno_rejects_ambiguous():
    ok, _ = normalize_yesno("maybe")
    assert not ok


def test_validators_cover_every_field():
    assert set(VALIDATORS) == set(FIELDS)
