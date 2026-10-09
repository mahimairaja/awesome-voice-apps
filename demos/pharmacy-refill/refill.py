"""Pure logic for the pharmacy refill line: no LiveKit, no network.

Three patterns live here so they can be unit tested without a model:
1. Capture with confirmation: normalise what was heard, score it, and only
   accept it once the caller has confirmed the read-back.
2. Speak codes exactly: rewrite Rx numbers, postal codes and dates before TTS
   so they are read character by character, never as "four thousand...".
3. Redact before logging: dates, phone numbers, postal codes and emails are
   masked in anything that leaves the call (the call log, the screen log).
"""

import random
import re
import uuid
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Literal

FieldName = Literal["drug", "rx_number", "date_of_birth", "postal_code", "pickup_store"]
FIELDS: list[str] = list(FieldName.__args__)
FIELD_LABELS = {
    "drug": "medication",
    "rx_number": "Rx number",
    "date_of_birth": "date of birth",
    "postal_code": "postal code",
    "pickup_store": "pickup store",
}

# Sound-alike pairs a pharmacist double-checks on every refill.
SOUND_ALIKES: list[tuple[str, str]] = [
    ("metformin", "metoprolol"),
    ("hydroxyzine", "hydralazine"),
    ("lamotrigine", "lamivudine"),
    ("clonidine", "clonazepam"),
    ("losartan", "valsartan"),
    ("tramadol", "trazodone"),
]
OTHER_DRUGS = [
    "atorvastatin",
    "lisinopril",
    "levothyroxine",
    "sertraline",
    "omeprazole",
    "amoxicillin",
    "gabapentin",
    "rosuvastatin",
    "escitalopram",
    "montelukast",
    "amlodipine",
    "prednisone",
]
FORMULARY = sorted({d for pair in SOUND_ALIKES for d in pair} | set(OTHER_DRUGS))
STRENGTHS = {
    "metformin": "500 mg",
    "metoprolol": "50 mg",
    "hydroxyzine": "25 mg",
    "hydralazine": "25 mg",
    "lamotrigine": "100 mg",
    "lamivudine": "150 mg",
    "clonidine": "0.1 mg",
    "clonazepam": "0.5 mg",
    "losartan": "50 mg",
    "valsartan": "80 mg",
    "tramadol": "50 mg",
    "trazodone": "50 mg",
}
# Tall Man lettering: capitalise the letters that differ between look-alikes.
TALL_MAN = {
    "metformin": "metFORMIN",
    "metoprolol": "metOPROLOL",
    "hydroxyzine": "hydrOXYzine",
    "hydralazine": "hydrALAZINE",
    "lamotrigine": "lamoTRIgine",
    "lamivudine": "lamiVUDine",
    "clonidine": "cloNIDine",
    "clonazepam": "clonazePAM",
    "losartan": "LOsartan",
    "valsartan": "VALsartan",
    "tramadol": "traMADol",
    "trazodone": "traZODone",
}
STORES = {
    "king": "King Street West",
    "danforth": "Danforth Avenue",
    "queen": "Queen Street East",
}
PHARMACY = "Larkfield Pharmacy"
PATIENTS = ["Jordan Ellis", "Sam Okafor", "Priya Nair", "Alex Moreau", "Taylor Brooks"]
# Letters that blur on a phone line; every Rx suffix comes from this set.
CONFUSABLE_LETTERS = "BDEPTVCGZ"
# Canada Post never uses D, F, I, O, Q or U, and never starts with W or Z.
POSTAL_FIRST = "ABCEGHJKLMNPRSTVXY"
POSTAL_OTHER = "ABCEGHJKLMNPRSTVWXYZ"
POSTAL_RE = re.compile(r"^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]\d[ABCEGHJ-NPRSTV-Z]\d$")
RX_RE = re.compile(r"^(\d{4})([A-Z])$")

NATO = {
    "A": "Alpha",
    "B": "Bravo",
    "C": "Charlie",
    "D": "Delta",
    "E": "Echo",
    "F": "Foxtrot",
    "G": "Golf",
    "H": "Hotel",
    "I": "India",
    "J": "Juliet",
    "K": "Kilo",
    "L": "Lima",
    "M": "Mike",
    "N": "November",
    "O": "Oscar",
    "P": "Papa",
    "Q": "Quebec",
    "R": "Romeo",
    "S": "Sierra",
    "T": "Tango",
    "U": "Uniform",
    "V": "Victor",
    "W": "Whiskey",
    "X": "X-ray",
    "Y": "Yankee",
    "Z": "Zulu",
}
DIGIT_WORDS = {
    "zero": "0",
    "oh": "0",
    "o": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
SPOKEN_DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
WORD_LETTERS = {name.lower(): letter for letter, name in NATO.items()} | {
    "bee": "B",
    "dee": "D",
    "ee": "E",
    "pee": "P",
    "tee": "T",
    "vee": "V",
    "see": "C",
    "gee": "G",
    "zed": "Z",
    "zee": "Z",
}


def new_profile(rng: random.Random | None = None, today: date | None = None) -> dict:
    """The prescription on file for this call, shown on screen as the bottle label."""
    rng = rng or random.Random()
    today = today or date.today()
    drug = rng.choice([d for pair in SOUND_ALIKES for d in pair])
    birth = date(today.year - rng.randint(28, 76), rng.randint(1, 12), rng.randint(1, 28))
    postal = (
        rng.choice(POSTAL_FIRST)
        + str(rng.randint(0, 9))
        + rng.choice(POSTAL_OTHER)
        + str(rng.randint(0, 9))
        + rng.choice(POSTAL_OTHER)
        + str(rng.randint(0, 9))
    )
    return {
        "patient": rng.choice(PATIENTS),
        "drug": drug,
        "strength": STRENGTHS[drug],
        "rx_number": f"{rng.randint(1000, 9999)}-{rng.choice(CONFUSABLE_LETTERS)}",
        "date_of_birth": birth.isoformat(),
        "postal_code": f"{postal[:3]} {postal[3:]}",
        "refills_left": rng.randint(1, 4),
    }


def initial_state(profile: dict | None = None) -> dict:
    return {
        "profile": profile or new_profile(),
        "fields": {
            f: {"heard": "", "value": "", "status": "empty", "score": 0, "tries": 0} for f in FIELDS
        },
        "spoken": [],
        "log": [],
        "ref": None,
    }


def _score(a: str, b: str) -> int:
    return round(100 * SequenceMatcher(None, a.lower(), b.lower()).ratio())


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z]+|\d", value.lower().replace("as in", " "))


def _characters(value: str) -> str:
    """Turn "four four seven one dash bee" or "4471 B as in Bravo" into "4471B"."""
    out = []
    for token in _tokens(value):
        if token.isdigit():
            out.append(token)
        elif token in DIGIT_WORDS and token not in {"o"}:
            out.append(DIGIT_WORDS[token])
        elif token in WORD_LETTERS:
            # "B as in Bravo" yields B then Bravo: keep one letter.
            letter = WORD_LETTERS[token]
            if not (out and out[-1] == letter and len(token) > 1):
                out.append(letter)
        elif len(token) == 1:
            out.append(token.upper())
        elif token == "dash":
            continue
        else:
            # A plain run like "m5v" or "abc" arrives as one token.
            out.extend(token.upper())
    return "".join(out)


def normalize_drug(heard: str) -> tuple[bool, str, str]:
    """Return (ok, drug or reason, note). Ambiguous sound-alikes are refused."""
    word = re.sub(r"[^a-z]", "", heard.lower().split(" ")[0] if heard.strip() else "")
    if not word:
        return False, "the medication name came through empty", ""
    ranked = sorted(FORMULARY, key=lambda d: SequenceMatcher(None, word, d).ratio(), reverse=True)
    best, second = ranked[0], ranked[1]
    best_score = SequenceMatcher(None, word, best).ratio()
    if best_score < 0.6:
        return False, f'"{heard}" is not on the formulary; ask them to spell it', ""
    if best_score < 0.9 and SequenceMatcher(None, word, second).ratio() > best_score - 0.1:
        return (
            False,
            f"that could be {best} or {second}; ask which one, and to spell the first letters",
            "",
        )
    note = ""
    for a, b in SOUND_ALIKES:
        if best in (a, b):
            other = b if best == a else a
            note = (
                f"{best} has a sound-alike, {other}. Read it back as {best}, "
                f"spelling the first five letters."
            )
    return True, best, note


def normalize_rx(heard: str) -> tuple[bool, str]:
    match = RX_RE.match(_characters(heard))
    if not match:
        return False, "an Rx number is four digits then one letter, like 4471-B"
    return True, f"{match.group(1)}-{match.group(2)}"


def normalize_postal(heard: str) -> tuple[bool, str]:
    chars = _characters(heard)
    if not POSTAL_RE.match(chars):
        return False, "a postal code is letter, digit, letter, digit, letter, digit, like M5V 2T6"
    return True, f"{chars[:3]} {chars[3:]}"


def normalize_dob(heard: str, today: date | None = None) -> tuple[bool, str]:
    today = today or date.today()
    try:
        d = datetime.strptime(heard.strip(), "%Y-%m-%d").date()
    except ValueError:
        return False, "pass the date of birth as year-month-day, like 1982-03-14"
    if not date(today.year - 120, 1, 1) <= d < today:
        return False, "that date of birth is not possible"
    return True, d.isoformat()


def normalize_store(heard: str) -> tuple[bool, str]:
    words = heard.lower()
    for key, store in STORES.items():
        if key in words:
            return True, store
    stores = ", ".join(STORES.values())
    return False, f"pickup is only at {stores}"


def capture(state: dict, field: str, heard: str, today: date | None = None) -> str:
    """Store what was heard, unconfirmed. Returns the tool result for the model."""
    slot = state["fields"][field]
    note = ""
    if field == "drug":
        ok, result, note = normalize_drug(heard)
    elif field == "rx_number":
        ok, result = normalize_rx(heard)
    elif field == "date_of_birth":
        ok, result = normalize_dob(heard, today)
    elif field == "postal_code":
        ok, result = normalize_postal(heard)
    else:
        ok, result = normalize_store(heard)
    slot["heard"] = heard.strip()[:80]
    slot["tries"] += 1
    if not ok:
        slot.update(value="", status="rejected", score=0)
        return f"rejected: {result}. Ask again."
    expected = state["profile"].get(field)
    slot.update(value=result, status="heard", score=_score(result, expected) if expected else 100)
    readback = f'Read it back exactly as "{result}" and ask them to confirm. {note}'.strip()
    return f"heard {FIELD_LABELS[field]}: {result}. {readback}"


def confirm(state: dict, field: str) -> str:
    """The caller said yes to the read-back. Check it against the file."""
    slot = state["fields"][field]
    if slot["status"] not in ("heard", "confirmed"):
        return f"nothing to confirm for {FIELD_LABELS[field]} yet; capture it first"
    expected = state["profile"].get(field)
    if field == "pickup_store" or slot["value"] == expected:
        slot.update(status="confirmed", score=100)
        return f"confirmed {FIELD_LABELS[field]}"
    slot["status"] = "mismatch"
    if field == "drug":
        return (
            "mismatch: that Rx is not for this medication. Do not say what is on file. "
            "Ask them to read the medication name from the label and spell it."
        )
    return (
        f"mismatch: the {FIELD_LABELS[field]} does not match the prescription on file. "
        "Do not say what is on file. Ask them to read it again, one character at a time."
    )


def place_refill(state: dict, today: date | None = None) -> str:
    missing = [FIELD_LABELS[f] for f in FIELDS if state["fields"][f]["status"] != "confirmed"]
    if missing:
        return f"cannot refill yet, still unconfirmed: {', '.join(missing)}"
    if not state["ref"]:
        today = today or date.today()
        state["ref"] = f"RF-{today:%m%d}-{uuid.uuid4().hex[:4].upper()}"
    store = state["fields"]["pickup_store"]["value"]
    return f"Refill placed. Reference {state['ref']}. Ready at {store} after 4 pm today."


# --- Speaking codes exactly ------------------------------------------------

_RX_SPOKEN = re.compile(r"\b(\d{4})-?([A-Z])\b")
_POSTAL_SPOKEN = re.compile(r"\b([A-Z]\d[A-Z]) ?(\d[A-Z]\d)\b")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _spell(chars: str) -> str:
    parts = []
    for c in chars:
        parts.append(SPOKEN_DIGITS[int(c)] if c.isdigit() else f"{c} as in {NATO[c]}")
    return ", ".join(parts)


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _spoken_date(m: re.Match) -> str:
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return m.group(0)
    return f"{d:%B} {_ordinal(d.day)}, {d.year}"


_POSTAL_HEAD = re.compile(r"[A-Z]\d[A-Z]$")


def split_ready(buffer: str) -> tuple[str, str]:
    """Split streamed text at the last safe word break, never inside a code."""
    cut = max(buffer.rfind(" "), buffer.rfind("\n"))
    if cut >= 0 and _POSTAL_HEAD.search(buffer[:cut]):
        cut = max(buffer.rfind(" ", 0, cut), buffer.rfind("\n", 0, cut))
    if cut < 0:
        return "", buffer
    return buffer[: cut + 1], buffer[cut + 1 :]


def speakable(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Rewrite codes for TTS. Returns the new text and each (written, spoken) pair."""
    pairs: list[tuple[str, str]] = []

    def swap(fn):
        def inner(m: re.Match) -> str:
            spoken = fn(m)
            if spoken != m.group(0):
                pairs.append((m.group(0), spoken))
            return spoken

        return inner

    text = _ISO_DATE.sub(swap(_spoken_date), text)
    text = _POSTAL_SPOKEN.sub(swap(lambda m: _spell(m.group(1)) + ". " + _spell(m.group(2))), text)
    text = _RX_SPOKEN.sub(swap(lambda m: _spell(m.group(1) + m.group(2))), text)
    return text, pairs


# --- Redaction --------------------------------------------------------------

MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
NUMBER_WORDS = (
    "zero|oh|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen"
    "|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy"
    "|eighty|ninety|hundred|thousand|first|second|third|fourth|fifth|sixth|seventh|eighth"
    "|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth"
    "|eighteenth|nineteenth|twentieth|thirtieth"
)
_NUM = rf"(?:\d+(?:st|nd|rd|th)?|{NUMBER_WORDS})"
_REDACTIONS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")),
    ("DOB", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("DOB", re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")),
    (
        "DOB",
        re.compile(
            rf"\b(?:{_NUM}\s+){{1,2}}of\s+(?:{MONTHS})\b[\s,]*(?:{_NUM}[\s,-]*){{0,4}}", re.I
        ),
    ),
    (
        "DOB",
        re.compile(
            rf"\b(?:{MONTHS})\b[\s,]*(?:(?:the\s+)?{_NUM}(?:[\s,-]+|$)){{1,6}}",
            re.IGNORECASE,
        ),
    ),
    (
        "PHONE",
        re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"),
    ),
    (
        "PHONE",
        re.compile(
            r"\b(?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|\d)[\s,.-]+){9,}"
            r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|\d)\b",
            re.IGNORECASE,
        ),
    ),
]
_POSTAL_TOKEN = r"(?:[a-z]|\d|zero|one|two|three|four|five|six|seven|eight|nine)"


def _postal_spans(text: str) -> list[tuple[int, int]]:
    """Find postal codes spoken as "M five V, two T six" or written as "m5v 2t6"."""
    spans = []
    tokens = list(re.finditer(rf"\b{_POSTAL_TOKEN}\b", text, re.I))
    i = 0
    while i + 6 <= len(tokens):
        run = tokens[i : i + 6]
        gaps = (text[a.end() : b.start()] for a, b in zip(run, run[1:]))
        chars = _characters(" ".join(t.group(0) for t in run))
        if all(re.fullmatch(r"[\s,.-]*", gap) for gap in gaps) and POSTAL_RE.match(chars):
            spans.append((run[0].start(), run[-1].end()))
            i += 6
        else:
            i += 1
    for m in re.finditer(r"\b[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d\b", text):
        spans.append(m.span())
    return spans


def redact(text: str) -> tuple[str, list[str]]:
    """Mask PII before a line is logged or shown. Returns the line and the tags hit."""
    tags: list[str] = []
    for tag, pattern in _REDACTIONS:
        text, n = pattern.subn(
            lambda m: f"[{tag}]" + (" " if m.group(0)[-1:].isspace() else ""), text
        )
        if n:
            tags.append(tag)
    spans = _postal_spans(text)
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + "[POSTAL]" + text[end:]
    if spans:
        tags.append("POSTAL")
    return re.sub(r"\s{2,}", " ", text).strip(), sorted(set(tags))


def snapshot(state: dict) -> dict:
    """The screen state: the bottle label, each field, read-backs and the redacted log."""
    p = state["profile"]
    return {
        "label": {
            "pharmacy": PHARMACY,
            "patient": p["patient"],
            "drug": TALL_MAN.get(p["drug"], p["drug"]),
            "strength": p["strength"],
            "rx": p["rx_number"],
            "dob": p["date_of_birth"],
            "postal": p["postal_code"],
            "refills": p["refills_left"],
            "stores": list(STORES.values()),
        },
        "fields": [
            {
                "field": f,
                "label": FIELD_LABELS[f],
                "heard": state["fields"][f]["heard"],
                "value": (
                    TALL_MAN.get(state["fields"][f]["value"], state["fields"][f]["value"])
                    if f == "drug"
                    else state["fields"][f]["value"]
                ),
                "status": state["fields"][f]["status"],
                "score": state["fields"][f]["score"],
            }
            for f in FIELDS
        ],
        "spoken": state["spoken"][-3:],
        "log": state["log"][-6:],
        "ref": state["ref"],
    }
