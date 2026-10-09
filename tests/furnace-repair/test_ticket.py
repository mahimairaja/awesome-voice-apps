from agent import normalize_address, normalize_phone, priority, spoken_digits, ticket_rows


def test_phone_accepts_spoken_forms_and_reads_back_digits():
    ok, value = normalize_phone("+1 416 555 0142")
    assert ok and value == "(416) 555-0142"
    assert spoken_digits(value) == "4 1 6, 5 5 5, 0 1 4 2"


def test_phone_rejects_short_or_invalid_numbers():
    assert normalize_phone("416 555 01")[0] is False
    assert normalize_phone("016 555 0142")[0] is False


def test_address_needs_a_house_number():
    assert normalize_address(" 42  Maple Street, Barrie. ") == (True, "42 Maple Street, Barrie")
    assert normalize_address("Maple Street")[0] is False


def test_priority_puts_gas_first():
    assert priority({"gas": True, "vulnerable": True}) == "gas: leave the home"
    assert priority({"gas": False, "vulnerable": True}).startswith("emergency")
    rows = ticket_rows({"problem": "no heat"})
    assert rows[0] == {"label": "problem", "value": "no heat"}
    assert rows[-1] == {"label": "priority", "value": "-"}
