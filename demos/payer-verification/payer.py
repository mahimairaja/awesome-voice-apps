"""Pure logic for the payer verification call: no LiveKit, no network.

Three pieces, each testable on its own:
- PayerLine: a simulated insurer phone tree. Menu keys are shuffled per call,
  so the agent has to listen to the menu instead of replaying a script.
- Hold and human detection signals for what the line says while on hold.
- The eligibility record: every value the rep says lands here, is normalised,
  checked, and kept with the words it came from.

Plus the tones the caller hears: ringback, keypad (DTMF) and hold music.
"""

import random
import re
from dataclasses import dataclass, field

import numpy as np

PAYER = "Harborline Health"
PRACTICE = "Lakeview Family Medicine"
# The canonical valid example NPI (it passes the Luhn check with the 80840 prefix).
NPI = "1234567893"
AGENT_NAME = "Ava"

CASE = {
    "patient": "Maria Delgado",
    "dob": "1986-03-14",
    "member_id": "HBH 448 213 907",
    "group": "N-20417",
    "service": "MRI of the brain without contrast",
    "cpt": "70551",
}

SAMPLE_RATE = 24000
FRAME = SAMPLE_RATE // 50  # 20 ms

# region: the phone tree


@dataclass
class Menu:
    id: str
    intro: str
    options: list[tuple[str, str]] = field(default_factory=list)  # (topic, key)
    goal: str = ""

    def prompt(self) -> str:
        spoken = " ".join(f"For {topic}, press {key}." for topic, key in self.options)
        return f"{self.intro} {spoken}".strip()

    def key_for(self, topic: str) -> str:
        return next(key for name, key in self.options if name == topic)


def luhn_npi(npi: str) -> bool:
    """NPIs are ten digits with a Luhn check digit over the prefix 80840."""
    if not re.fullmatch(r"\d{10}", npi):
        return False
    digits = [int(d) for d in "80840" + npi]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
    return total % 10 == 0


def clean_keys(keys: str) -> str:
    """Keep only what a keypad can send."""
    return "".join(ch for ch in (keys or "") if ch in "0123456789*#")[:16]


class PayerLine:
    """The insurer's IVR: welcome menu, NPI entry, department menu, then hold."""

    MAX_TRIES = 2

    def __init__(self, seed: int | None = None) -> None:
        rng = random.Random(seed)
        welcome = ["members", "providers and doctor's offices", "pharmacies"]
        departments = [
            "claim status",
            "eligibility and benefits",
            "prior authorization",
            "payments",
        ]
        self.menus = {
            "welcome": Menu(
                "welcome",
                f"Thank you for calling {PAYER}. Calls may be recorded.",
                list(zip(rng.sample(welcome, 3), "123")),
                goal="providers and doctor's offices",
            ),
            "npi": Menu("npi", "Please enter your ten digit N P I, followed by the pound key."),
            "department": Menu(
                "department",
                "Thank you.",
                list(zip(rng.sample(departments, 4), "1234")),
                goal="eligibility and benefits",
            ),
        }
        self.node = "welcome"
        self.tries = 0

    def prompt(self) -> str:
        return self.menus[self.node].prompt()

    def expected(self) -> str:
        """The right keys for the current menu: used only when the model fails."""
        menu = self.menus[self.node]
        return f"{NPI}#" if menu.id == "npi" else menu.key_for(menu.goal)

    def press(self, keys: str) -> tuple[bool, str]:
        """Apply keys to the current menu. Returns (accepted, what the line says next)."""
        keys = clean_keys(keys)
        menu = self.menus[self.node]
        if menu.id == "npi":
            ok = keys.endswith("#") and luhn_npi(keys[:-1])
            nxt = "department"
            sorry = "That N P I was not recognized."
        else:
            ok = len(keys) == 1 and any(keys == key for _, key in menu.options)
            chosen = next((topic for topic, key in menu.options if key == keys), None)
            nxt = "npi" if menu.id == "welcome" else "hold"
            sorry = "Sorry, that is not a valid option."
            if ok and chosen != menu.goal:
                # A real menu would route elsewhere; this demo only staffs one queue.
                ok, sorry = False, f"The {chosen} line is closed in this simulation."
        if ok:
            self.node, self.tries = nxt, 0
            return True, ""
        self.tries += 1
        if self.tries >= self.MAX_TRIES:
            # Payers usually fall through to a person after repeated misses.
            self.node, self.tries = "hold", 0
            return False, f"{sorry} Transferring you to a representative."
        return False, sorry

    @property
    def on_hold(self) -> bool:
        return self.node == "hold"


HOLD_GREETING = (
    "Please hold for the next available representative. "
    "Your estimated wait time is under two minutes."
)
HOLD_MESSAGE = f"Thank you for holding. Your call is important to {PAYER}. Please stay on the line."

# endregion

# region: hold and human detection

# Scripted phrases that recordings use and people rarely do.
HOLD_PHRASES = (
    "your call is important",
    "please hold",
    "continue to hold",
    "stay on the line",
    "next available",
    "estimated wait",
    "calls may be recorded",
    "thank you for holding",
    "thank you for your patience",
    "in the order it was received",
    "visit our website",
    "press",
)
HUMAN_CUES = (
    "this is",
    "my name is",
    "speaking",
    "how can i help",
    "how may i help",
    "can i get",
    "can i have",
    "who am i speaking",
    "what's the",
    "what is the",
    "npi",
    "member id",
)


def hold_signals(text: str, heard: list[str]) -> dict:
    """Cheap evidence about whether a line on hold is a recording or a person."""
    low = text.lower()
    scripted = [p for p in HOLD_PHRASES if p in low]
    cues = [c for c in HUMAN_CUES if c in low]
    norm = re.sub(r"[^a-z ]", "", low).strip()
    repeated = bool(norm) and any(re.sub(r"[^a-z ]", "", h.lower()).strip() == norm for h in heard)
    return {"scripted": scripted[:3], "cues": cues[:3], "repeated": repeated}


def fallback_verdict(signals: dict) -> str:
    """Used only if the classifier model fails: a recording unless a person is obvious."""
    if signals["repeated"] or signals["scripted"]:
        return "recording"
    return "human" if signals["cues"] else "recording"


# endregion

# region: the eligibility record

FIELDS: list[tuple[str, str]] = [
    ("coverage", "Coverage"),
    ("plan", "Plan"),
    ("network", "Network"),
    ("copay", "Specialist copay"),
    ("deductible", "Deductible"),
    ("deductible_met", "Met so far"),
    ("coinsurance", "Coinsurance"),
    ("prior_auth", "Prior auth, MRI 70551"),
    ("reference", "Call reference"),
    ("rep", "Rep name"),
]
LABELS = dict(FIELDS)
REQUIRED = (
    "coverage",
    "copay",
    "deductible",
    "deductible_met",
    "coinsurance",
    "prior_auth",
    "reference",
)
# An inactive plan has no benefits to read: only the status and the reference.
REQUIRED_INACTIVE = ("coverage", "reference")
MONEY = {"copay": 2000, "deductible": 50000, "deductible_met": 50000}

# Tool argument name -> record field.
ARGS = {
    "coverage": "coverage",
    "plan": "plan",
    "network": "network",
    "specialist_copay_usd": "copay",
    "deductible_usd": "deductible",
    "deductible_met_usd": "deductible_met",
    "coinsurance_percent": "coinsurance",
    "prior_auth_required": "prior_auth",
    "reference_number": "reference",
    "rep_name": "rep",
}


def new_record() -> dict:
    return {
        name: {"value": None, "display": "", "status": "empty", "source": "", "prev": ""}
        for name, _ in FIELDS
    }


def money(value: float) -> str:
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def normalise(name: str, raw) -> tuple[object, str] | str:
    """Return (value, display) or an error string for the model."""
    if name in MONEY:
        try:
            value = round(float(raw), 2)
        except (TypeError, ValueError):
            return f"{LABELS[name]} must be a dollar amount"
        if not 0 <= value <= MONEY[name]:
            return f"{LABELS[name]} of {money(value)} is out of range; ask the rep to repeat it"
        return value, money(value)
    if name == "coinsurance":
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return "coinsurance must be a percentage"
        value = value * 100 if 0 < value < 1 else value
        if not 0 <= value <= 100:
            return f"coinsurance of {value:g}% is out of range; ask the rep to repeat it"
        value = round(value, 1)
        return value, f"{value:g}%"
    if name == "coverage":
        value = str(raw).lower().strip()
        if value not in {"active", "inactive"}:
            return "coverage must be active or inactive"
        return value, value.capitalize()
    if name == "network":
        value = str(raw).lower().strip()
        if value not in {"in", "out"}:
            return "network must be in or out"
        return value, "In network" if value == "in" else "Out of network"
    if name == "prior_auth":
        if not isinstance(raw, bool):
            return "prior auth must be yes or no"
        return raw, "Required" if raw else "Not required"
    if name == "reference":
        value = re.sub(r"[^A-Za-z0-9]", "", str(raw)).upper()
        if not 4 <= len(value) <= 16:
            return "the reference number should be 4 to 16 letters or digits; ask for it again"
        return value, value
    value = " ".join(str(raw).split())[:40]
    if not value:
        return f"{LABELS[name]} was empty"
    return value, value


def required(record: dict) -> tuple[str, ...]:
    return REQUIRED_INACTIVE if record["coverage"]["value"] == "inactive" else REQUIRED


def missing(record: dict) -> list[str]:
    return [n for n in required(record) if record[n]["status"] in {"empty", "conflict"}]


def apply(record: dict, updates: dict, source: str) -> str:
    """Merge what the rep just said into the record. Returns the tool result.

    `updates` uses record field names; None means "not mentioned this turn".
    A new value for a filled field is a correction: the old one is kept as
    `prev` so the page can show it struck through.
    """
    recorded, problems = [], []
    source = " ".join(source.split())[:140]
    for name, raw in updates.items():
        if raw is None or name not in record:
            continue
        result = normalise(name, raw)
        if isinstance(result, str):
            problems.append(result)
            continue
        value, display = result
        slot = record[name]
        if slot["value"] == value and slot["status"] != "conflict":
            continue
        corrected = slot["value"] is not None
        slot.update(
            prev=slot["display"] if corrected else slot["prev"],
            value=value,
            display=display,
            status="corrected" if corrected else "heard",
            source=source,
        )
        recorded.append(f"{LABELS[name]} {display}" + (" (corrected)" if corrected else ""))

    ded, met = record["deductible"], record["deductible_met"]
    if ded["value"] is not None and met["value"] is not None:
        if met["value"] > ded["value"]:
            met["status"] = "conflict"
            problems.append(
                f"met {met['display']} is more than the {ded['display']} deductible; "
                "ask the rep to confirm both"
            )
        elif met["status"] == "conflict":
            met["status"] = "corrected"

    parts = []
    if recorded:
        parts.append("Recorded: " + "; ".join(recorded) + ".")
    if problems:
        parts.append("Problems: " + "; ".join(problems) + ".")
    gaps = missing(record)
    if gaps:
        parts.append("Still needed: " + ", ".join(LABELS[n] for n in gaps) + ".")
    else:
        parts.append("Everything required is captured: call finish_verification.")
    return " ".join(parts) or "Nothing new to record."


def remaining(record: dict) -> str:
    ded, met = record["deductible"], record["deductible_met"]
    if ded["value"] is None or met["value"] is None or met["status"] == "conflict":
        return ""
    return money(max(0.0, ded["value"] - met["value"]))


def summary(record: dict) -> dict:
    """The structured result, shaped like the benefits part of an X12 271 response."""
    v = {name: slot["value"] for name, slot in record.items()}
    return {
        "payer": PAYER,
        "member_id": CASE["member_id"].replace(" ", ""),
        "coverage": v["coverage"],
        "plan": v["plan"],
        "in_network": None if v["network"] is None else v["network"] == "in",
        "specialist_copay_usd": v["copay"],
        "deductible_usd": v["deductible"],
        "deductible_met_usd": v["deductible_met"],
        "coinsurance_percent": v["coinsurance"],
        "prior_auth": {"cpt": CASE["cpt"], "required": v["prior_auth"]},
        "reference": v["reference"],
        "rep": v["rep"],
    }


# endregion

# region: the rep's screen (what the visitor reads from)


def rep_screen(seed: int | None = None) -> dict:
    """Made-up benefits for the visitor to read out, different on every call."""
    rng = random.Random(seed)
    deductible = rng.choice([1000, 1500, 2000, 2500])
    return {
        "coverage": "Active",
        "plan": rng.choice(["PPO Choice", "Open Access Plus", "Select PPO"]),
        "network": "In network",
        "copay": money(rng.choice([40, 50, 60, 75])),
        "deductible": money(deductible),
        "deductible_met": money(rng.choice([180, 320, 420, 610])),
        "coinsurance": f"{rng.choice([10, 20, 30])}%",
        "prior_auth": "Required",
        "reference": f"HB{rng.randint(10, 99)}{rng.choice('KMRTX')}{rng.randint(100, 999)}",
    }


# endregion

# region: the sounds of a phone call

DTMF_FREQ = {
    "1": (697, 1209), "2": (697, 1336), "3": (697, 1477),
    "4": (770, 1209), "5": (770, 1336), "6": (770, 1477),
    "7": (852, 1209), "8": (852, 1336), "9": (852, 1477),
    "*": (941, 1209), "0": (941, 1336), "#": (941, 1477),
}  # fmt: skip
# RFC 4733 event codes, as LiveKit's publish_dtmf expects them.
DTMF_CODE = {**{str(d): d for d in range(10)}, "*": 10, "#": 11}


def _tone(freqs: tuple[float, ...], seconds: float, level: float) -> np.ndarray:
    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    wave = sum(np.sin(2 * np.pi * f * t) for f in freqs) / len(freqs)
    ramp = min(len(t) // 2, int(SAMPLE_RATE * 0.005))
    if ramp:
        env = np.ones(len(t))
        env[:ramp] = np.linspace(0, 1, ramp)
        env[-ramp:] = np.linspace(1, 0, ramp)
        wave = wave * env
    return wave * level


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * seconds))


def _pcm(wave: np.ndarray) -> np.ndarray:
    return (np.clip(wave, -1, 1) * 32767).astype(np.int16)


def dtmf_audio(keys: str) -> np.ndarray:
    """Each key as 100 ms of its two tones, then a 60 ms gap."""
    parts = []
    for key in clean_keys(keys):
        parts += [_tone(DTMF_FREQ[key], 0.1, 0.22), _silence(0.06)]
    return _pcm(np.concatenate(parts) if parts else _silence(0.02))


def ringback_audio(rings: int = 1) -> np.ndarray:
    """North American ringback: 440 + 480 Hz, two seconds on, then a short gap."""
    parts = []
    for _ in range(rings):
        parts += [_tone((440, 480), 2.0, 0.12), _silence(0.8)]
    return _pcm(np.concatenate(parts))


def hold_music(seconds: float = 8.0) -> np.ndarray:
    """A soft, looping arpeggio: unmistakably hold music, and cheap to make."""
    notes = [261.63, 329.63, 392.0, 523.25, 392.0, 329.63, 293.66, 349.23]
    step = 0.5
    out = []
    t = np.arange(int(SAMPLE_RATE * step)) / SAMPLE_RATE
    decay = np.exp(-t * 4)
    for i in range(int(seconds / step)):
        f = notes[i % len(notes)]
        out.append((np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * decay * 0.07)
    return _pcm(np.concatenate(out))


def frames(pcm: np.ndarray) -> list[np.ndarray]:
    """Split PCM into 20 ms chunks, padding the last one."""
    pad = (-len(pcm)) % FRAME
    if pad:
        pcm = np.concatenate([pcm, np.zeros(pad, dtype=np.int16)])
    return [pcm[i : i + FRAME] for i in range(0, len(pcm), FRAME)]


# endregion
