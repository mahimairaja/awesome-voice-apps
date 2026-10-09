"""Score a spoken reading of the service card against what is printed on it.

The caller reads a street address and an account number from their screen. The
agent never sees the card; this module compares the speech-to-text transcript
with it, token by token, so the panel can show how much of the reading survived
the line.
"""

import random
import re

STREETS = (
    "Thistlewood",
    "Ashgrove",
    "Kinsella",
    "Okanagan",
    "Wetherby",
    "Marchmont",
    "Beauchamp",
    "Lachance",
)
STREET_TYPES = ("Crescent", "Road", "Lane", "Drive", "Court")

UNITS = {
    "zero": 0,
    "oh": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
ABBREVIATIONS = {
    "cres": "crescent",
    "cr": "crescent",
    "rd": "road",
    "ln": "lane",
    "dr": "drive",
    "ct": "court",
}
REPEATS = {"double": 2, "triple": 3}


def new_card(rng: random.Random | None = None) -> dict:
    """A made-up service card: a street address and an eight-digit account."""
    rng = rng or random.Random()
    account = "".join(str(rng.randrange(10)) for _ in range(8))
    return {
        "house": str(rng.randrange(12, 99)),
        "street": f"{rng.choice(STREETS)} {rng.choice(STREET_TYPES)}",
        "account": f"{account[:4]} {account[4:]}",
    }


def card_address(card: dict) -> str:
    return f"{card['house']} {card['street']}"


def expected_tokens(card: dict) -> list[str]:
    return tokens(f"{card_address(card)} {card['account']}")


def digits(text: str) -> str:
    """Only the digits of a spoken or typed number, with number words converted."""
    return "".join(t for t in tokens(text) if t.isdigit())


def tokens(text: str) -> list[str]:
    """Lowercase words, with every number spelled out as single digits.

    "Eighteen Thistlewood Cres, account 4471-2093" and "one eight thistlewood
    crescent account four four seven one two oh nine three" both give
    ["1", "8", "thistlewood", "crescent", "account", "4", "4", ...].
    """
    words = re.sub(r"[^a-z0-9]+", " ", text.lower().replace("-", " ")).split()
    out: list[str] = []
    repeat = 1
    i = 0
    while i < len(words):
        word = ABBREVIATIONS.get(words[i], words[i])
        if word in REPEATS:
            repeat = REPEATS[word]
            i += 1
            continue
        if word in TENS:
            value = TENS[word]
            if i + 1 < len(words) and 0 < UNITS.get(words[i + 1], 0) < 10:
                value += UNITS[words[i + 1]]
                i += 1
            out.extend(str(value) * repeat)
        elif word in UNITS:
            out.extend(str(UNITS[word]) * repeat)
        elif word.isdigit():
            out.extend(word * repeat)
        else:
            out.append(word)
        repeat = 1
        i += 1
    return out


def matched(expected: list[str], heard: list[str]) -> int:
    """How many expected tokens were heard, in order (longest common subsequence)."""
    row = [0] * (len(heard) + 1)
    for token in expected:
        previous = 0
        for j, candidate in enumerate(heard, start=1):
            current = row[j]
            row[j] = previous + 1 if token == candidate else max(row[j], row[j - 1])
            previous = current
    return row[-1]
