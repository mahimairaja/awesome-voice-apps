"""Pure logic for the post-op check-in: no LiveKit, no network.

The model talks and extracts answers; this module decides. Each answer is
checked against a fixed rule table, the most severe rule that fires sets the
escalation tier, and the next question always comes from the protocol order.
The model has no tool that lowers a tier or skips a question, so a patient
saying "it's probably nothing" changes nothing.

Brands, patients and the protocol are fictional and not medical advice.
"""

import random
from datetime import date, timedelta
from typing import Literal

HOSPITAL = "Northfield General"
PROTOCOL = "TKA-D3 v2.1"
PROTOCOL_NAME = "Total knee replacement, day 3 check-in"

StepId = Literal["breathing", "calf", "temperature", "wound", "pain"]
Drainage = Literal["none", "clear", "spotting", "cloudy_or_pus"]
TempUnit = Literal["F", "C", "unknown"]
Tier = Literal["routine", "nurse", "emergency"]

# Emergency screens first, as nurse triage protocols do: a patient with chest
# pain should not answer four questions about their incision before hearing 911.
STEPS: list[dict] = [
    {
        "id": "breathing",
        "label": "Breathing",
        "ask": "Since you got home, have you had any chest pain or shortness of breath?",
    },
    {
        "id": "calf",
        "label": "Calf",
        "ask": "Any new pain, swelling or tenderness in either calf?",
    },
    {
        "id": "temperature",
        "label": "Temperature",
        "ask": "Have you taken your temperature today? What did it read?",
    },
    {
        "id": "wound",
        "label": "Incision",
        "ask": (
            "How does the incision look? Any redness spreading out from it, any "
            "drainage, or are the edges opening?"
        ),
    },
    {
        "id": "pain",
        "label": "Pain",
        "ask": "On a scale of zero to ten, how is your pain, and is the pain medication helping?",
    },
]
STEP_IDS: list[str] = [s["id"] for s in STEPS]
STEP_BY_ID = {s["id"]: s for s in STEPS}

FEVER_F = 101.5
FEVER_C = 38.6

# The rule table. Code evaluates it; the model never sees thresholds as advice.
RULES: list[dict] = [
    {
        "id": "E1",
        "step": "breathing",
        "tier": "emergency",
        "when": "Chest pain or shortness of breath",
        "why": "possible blood clot in the lungs",
    },
    {
        "id": "N1",
        "step": "calf",
        "tier": "nurse",
        "when": "Calf pain, swelling or tenderness",
        "why": "possible blood clot in the leg",
    },
    {
        "id": "N2",
        "step": "temperature",
        "tier": "nurse",
        "when": f"Temperature {FEVER_F}°F ({FEVER_C}°C) or higher",
        "why": "fever after surgery",
    },
    {
        "id": "N3",
        "step": "temperature",
        "tier": "nurse",
        "when": "No reading, but chills or sweats",
        "why": "possible fever without a reading",
    },
    {
        "id": "N4",
        "step": "wound",
        "tier": "nurse",
        "when": "Cloudy or pus-like drainage",
        "why": "possible wound infection",
    },
    {
        "id": "N5",
        "step": "wound",
        "tier": "nurse",
        "when": "Redness spreading from the incision",
        "why": "possible wound infection",
    },
    {
        "id": "N6",
        "step": "wound",
        "tier": "nurse",
        "when": "Incision edges opening",
        "why": "wound separation",
    },
    {
        "id": "N7",
        "step": "pain",
        "tier": "nurse",
        "when": "Pain 8 or more and medication not helping",
        "why": "uncontrolled pain",
    },
]
RULE_BY_ID = {r["id"]: r for r in RULES}
TIER_RANK = {"routine": 0, "nurse": 1, "emergency": 2}

EMERGENCY_SCRIPT = (
    "Chest pain or trouble breathing after surgery needs emergency care. Please hang "
    "up and call 911 now. I am alerting the orthopedic care team."
)

PATIENTS = [
    ("Alex Morgan", "right"),
    ("Jordan Lee", "left"),
    ("Sam Rivera", "right"),
    ("Taylor Brooks", "left"),
]


def new_patient(rng: random.Random | None = None, today: date | None = None) -> dict:
    rng = rng or random.Random()
    today = today or date.today()
    name, side = rng.choice(PATIENTS)
    surgery = today - timedelta(days=3)
    return {
        "name": name,
        "procedure": f"Total knee replacement, {side}",
        "surgery_date": surgery.isoformat(),
        "day": 3,
        "surgeon": "Dr. Okafor",
        "hospital": HOSPITAL,
        "next_check_in": (surgery + timedelta(days=7)).isoformat(),
    }


def initial_state(patient: dict | None = None) -> dict:
    return {
        "patient": patient or new_patient(),
        "answers": {step: None for step in STEP_IDS},
        "outcome": None,
        "handoff": None,
    }


def current_step(state: dict) -> str | None:
    """The protocol, not the model, decides what is asked next."""
    if state["outcome"] and state["outcome"]["tier"] == "emergency":
        return None
    for step in STEP_IDS:
        if state["answers"][step] is None:
            return step
    return None


def fired_rules(state: dict) -> list[dict]:
    """Every rule that fired on the current answers, in table order."""
    fired = []
    for rule in RULES:
        answer = state["answers"][rule["step"]]
        if answer and rule["id"] in answer["observed"]:
            fired.append({**rule, "observed": answer["observed"][rule["id"]]})
    return fired


def tier(state: dict) -> Tier:
    """The most severe rule that fired. No tool can lower it."""
    worst = "routine"
    for rule in fired_rules(state):
        if TIER_RANK[rule["tier"]] > TIER_RANK[worst]:
            worst = rule["tier"]
    return worst


def to_fahrenheit(reading: float, unit: TempUnit) -> tuple[bool, float | str]:
    if unit == "unknown":
        # Nobody runs a 45 degree Fahrenheit fever: small numbers are Celsius.
        unit = "C" if reading < 45 else "F"
    f = reading * 9 / 5 + 32 if unit == "C" else reading
    if not 93 <= f <= 109:
        return False, f"{reading:g} is not a body temperature"
    return True, round(f, 1)


def _clip(words: str) -> str:
    return " ".join(words.split())[:120]


def _evaluate(step: str, value: dict) -> tuple[bool, str, list[str]]:
    """Validate one answer and return (ok, summary or error, rule ids that fired)."""
    if step == "breathing":
        bad = value["chest_pain_or_short_of_breath"]
        return (
            True,
            "Chest pain or short of breath" if bad else "No chest pain or breathlessness",
            (["E1"] if bad else []),
        )
    if step == "calf":
        bad = value["calf_pain_or_swelling"]
        return True, "Calf pain or swelling" if bad else "Calves normal", ["N1"] if bad else []
    if step == "temperature":
        reading, chills = value["reading"], value["chills_or_sweats"]
        if reading is None:
            summary = "No reading" + (", chills or sweats" if chills else ", no chills")
            return True, summary, ["N3"] if chills else []
        ok, f = to_fahrenheit(reading, value["unit"])
        if not ok:
            return False, f, []
        c = round((f - 32) * 5 / 9, 1)
        return True, f"{f:g}°F ({c:g}°C)", ["N2"] if f >= FEVER_F else []
    if step == "wound":
        rules, parts = [], []
        if value["drainage"] == "cloudy_or_pus":
            rules.append("N4")
        if value["redness_spreading"]:
            rules.append("N5")
            parts.append("spreading redness")
        if value["edges_opening"]:
            rules.append("N6")
            parts.append("edges opening")
        drainage = {
            "none": "no drainage",
            "clear": "clear drainage",
            "spotting": "light spotting",
            "cloudy_or_pus": "cloudy drainage",
        }[value["drainage"]]
        return True, ", ".join([drainage, *parts]).capitalize(), rules
    if step == "pain":
        score, helping = value["score"], value["medication_helping"]
        if not 0 <= score <= 10:
            return False, f"{score} is not on the zero to ten scale", []
        summary = f"{score}/10, medication {'helping' if helping else 'not helping'}"
        return True, summary, ["N7"] if score >= 8 and not helping else []
    raise ValueError(step)


def record(state: dict, step: str, value: dict, words: str = "") -> str:
    """Store one answer, run the rules, and tell the model what to say next."""
    if state["outcome"]:
        if state["outcome"]["tier"] == "emergency":
            return f"The check-in stopped on an emergency. Say only: {EMERGENCY_SCRIPT}"
        return "The check-in is finished. Say goodbye, or call start_over if they ask."
    ok, summary, rules = _evaluate(step, value)
    if not ok:
        return f"rejected: {summary}. Ask again: {STEP_BY_ID[step]['ask']}"
    previous = state["answers"][step]
    # A correction is recorded, never erased: a rule that fired stays fired, and
    # the nurse sees the earlier answer. Mistakes fail toward a callback.
    observed = dict(previous["observed"]) if previous else {}
    for rule_id in rules:
        observed.setdefault(rule_id, summary)
    state["answers"][step] = {
        "value": value,
        "summary": summary,
        "was": previous["summary"] if previous else "",
        "quote": _clip(words),
        "rules": [r["id"] for r in RULES if r["id"] in observed],
        "observed": observed,
    }
    if "E1" in rules:
        _close(state, "emergency")
        return (
            f"EMERGENCY: rule E1 fired. The check-in stops here. Say exactly: "
            f'"{EMERGENCY_SCRIPT}" Ask nothing else.'
        )
    said = []
    if previous and set(state["answers"][step]["rules"]) - set(rules):
        said.append("The earlier answer already fired a rule; it stays on the record.")
    for rule_id in rules:
        rule = RULE_BY_ID[rule_id]
        said.append(f"Rule {rule_id} fired ({rule['when'].lower()}).")
    if said:
        said.append(
            "Tell them in one short sentence that you are flagging this for the nurse. "
            "Do not reassure or advise."
        )
    nxt = current_step(state)
    if nxt:
        said.append(f"Next ask: {STEP_BY_ID[nxt]['ask']}")
    else:
        said.append("Every question is answered. Call finish_check_in now.")
    return f"recorded {STEP_BY_ID[step]['label'].lower()}: {summary}. " + " ".join(said)


def _ref(prefix: str, today: date | None = None) -> str:
    today = today or date.today()
    return f"{prefix}-{today:%m%d}-{random.randrange(16**4):04X}"


def _close(state: dict, final: Tier) -> None:
    state["outcome"] = {
        "tier": final,
        "ref": _ref({"emergency": "ER", "nurse": "NL", "routine": "CI"}[final]),
        "rules": [r["id"] for r in fired_rules(state)],
    }
    if final != "routine":
        state["handoff"] = handoff(state)


def _assess(step: dict, answer: dict) -> str:
    line = f"{step['label']}: {answer['summary']}"
    return f"{line}, earlier {answer['was']}" if answer["was"] else line


def handoff(state: dict) -> dict:
    """An SBAR note built from the answers, so the nurse never re-asks."""
    p = state["patient"]
    fired = fired_rules(state)
    answered = [s for s in STEPS if state["answers"][s["id"]]]
    return {
        "situation": "; ".join(f"{r['why'].capitalize()} (rule {r['id']})" for r in fired),
        "background": (
            f"{p['name']}, {p['procedure'].lower()}, surgery {p['surgery_date']} with "
            f"{p['surgeon']}, post-op day {p['day']}."
        ),
        "assessment": [_assess(s, state["answers"][s["id"]]) for s in answered],
        "recommendation": (
            "Patient told to call 911. Care team to follow up after the emergency visit."
            if tier(state) == "emergency"
            else "On-call orthopedic nurse to call back today."
        ),
    }


def finish(state: dict) -> str:
    """Close the check-in. The outcome comes from the rules, not from the model."""
    if state["outcome"]:
        if state["outcome"]["tier"] == "emergency":
            return f"Already escalated as an emergency. Say only: {EMERGENCY_SCRIPT}"
        return "Already finished. Say goodbye."
    missing = [STEP_BY_ID[s]["label"].lower() for s in STEP_IDS if state["answers"][s] is None]
    if missing:
        return (
            f"refused: the protocol still needs {', '.join(missing)}. "
            f"Ask: {STEP_BY_ID[current_step(state)]['ask']}"
        )
    final = tier(state)
    _close(state, final)
    ref = state["outcome"]["ref"]
    if final == "nurse":
        reasons = "; ".join(r["why"] for r in fired_rules(state))
        return (
            f"Outcome NURSE, reference {ref}. Say: because of {reasons}, the protocol "
            "sends this to the on-call orthopedic nurse today; a nurse will call them "
            "back and already has their answers, and if chest pain or trouble breathing "
            "starts they should call 911. Then say goodbye."
        )
    return (
        f"Outcome ROUTINE, reference {ref}. Say: every answer is within the recovery "
        f"protocol, keep up the exercises, and the next check-in is on "
        f"{state['patient']['next_check_in']}. Then say goodbye."
    )


def start_over(state: dict) -> str:
    """A demo control: only after the outcome is final, never to dodge a rule."""
    if not state["outcome"]:
        return "refused: finish the check-in first. Nothing is reset mid-protocol."
    patient = state["patient"]
    state.clear()
    state.update(initial_state(patient))
    return f"Reset. Ask: {STEPS[0]['ask']}"


def snapshot(state: dict) -> dict:
    """The whole panel in one UI event."""
    nxt = current_step(state)
    fired = {r["id"]: r["observed"] for r in fired_rules(state)}
    steps = []
    for s in STEPS:
        answer = state["answers"][s["id"]]
        if answer:
            worst = max(
                (RULE_BY_ID[r]["tier"] for r in answer["rules"]),
                key=TIER_RANK.get,
                default="routine",
            )
            status = {"routine": "clear", "nurse": "flag", "emergency": "emergency"}[worst]
        elif s["id"] == nxt:
            status = "current"
        elif state["outcome"]:
            status = "skipped"
        else:
            status = "pending"
        steps.append(
            {
                "id": s["id"],
                "label": s["label"],
                "ask": s["ask"],
                "status": status,
                "summary": answer["summary"] if answer else "",
                "quote": answer["quote"] if answer else "",
                "rules": answer["rules"] if answer else [],
                "was": answer["was"] if answer else "",
            }
        )
    outcome = state["outcome"]
    return {
        "patient": dict(state["patient"]),
        "protocol": {"id": PROTOCOL, "name": PROTOCOL_NAME},
        "steps": steps,
        "rules": [
            {
                "id": r["id"],
                "step": r["step"],
                "tier": r["tier"],
                "when": r["when"],
                "fired": r["id"] in fired,
                "observed": fired.get(r["id"], ""),
            }
            for r in RULES
        ],
        "tier": outcome["tier"] if outcome else tier(state),
        "outcome": dict(outcome) if outcome else None,
        "handoff": dict(state["handoff"]) if state["handoff"] else None,
    }
