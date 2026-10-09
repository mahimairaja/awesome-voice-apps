"""Intent routing for the Northvale support line, with no model or network.

Three pieces live here so they can be tested on their own:

- the classic press-1 menu, and how long a caller who knows exactly which keys
  to press still spends getting through it;
- the router's decision policy: route when one department is clearly ahead,
  otherwise ask one clarifying question, and never loop the caller more than
  twice;
- the per-call state and the snapshot the agent publishes to the screen.

The model never picks the queue. It reports probabilities through a one-token
classification (see agent.py), and `decide` turns them into an action.
"""

import math
import time
from dataclasses import dataclass, field

BRAND = "Northvale"

# One letter per queue so the classifier answers in a single token.
DEPARTMENTS: dict[str, dict[str, str]] = {
    "billing": {
        "letter": "A",
        "label": "Billing and payments",
        "covers": "charges, double charges, refunds, payment arrangements, autopay, "
        "late fees, explaining a bill",
    },
    "internet": {
        "letter": "B",
        "label": "Internet support",
        "covers": "home internet down or slow, Wi-Fi, modem or router problems",
    },
    "mobile": {
        "letter": "C",
        "label": "Mobile and SIM",
        "covers": "mobile plans and data, roaming, SIM or eSIM, a lost or stolen "
        "phone, porting a number",
    },
    "visit": {
        "letter": "D",
        "label": "Technician visits",
        "covers": "booking, checking, rescheduling or a missed technician or "
        "installation appointment",
    },
    "moving": {
        "letter": "E",
        "label": "Moving and new service",
        "covers": "moving service to a new address, adding a service, upgrading a plan",
    },
    "cancel": {
        "letter": "F",
        "label": "Cancellations",
        "covers": "cancelling a service or the whole account, a better offer elsewhere",
    },
}
UNCLEAR = "unclear"
UNCLEAR_LETTER = "X"
LETTERS = {d["letter"]: key for key, d in DEPARTMENTS.items()} | {UNCLEAR_LETTER: UNCLEAR}

# Route when the top department reaches this probability. Below it, ask.
ROUTE_AT = 0.75
# After this many clarifying questions, route to the best guess and flag it.
MAX_CLARIFY = 2

# A press-1 menu, read the way phone menus are: about 150 words a minute.
WORDS_PER_SECOND = 2.5
PAUSE_SECONDS = 0.4  # between menu lines
KEY_SECONDS = 1.0  # a caller pressing the key and the menu moving on


@dataclass(frozen=True)
class Option:
    key: str
    line: str
    to: "str | Menu | None" = None  # a department, a submenu, or a dead end


@dataclass(frozen=True)
class Menu:
    intro: str
    options: tuple[Option, ...] = field(default_factory=tuple)


MENU = Menu(
    f"Thank you for calling {BRAND}. Calls may be recorded for quality. Pour le service en "
    "français, faites le neuf. Please listen closely, as our menu options have changed.",
    (
        Option(
            "1",
            "For billing and payments, press 1.",
            Menu(
                "Billing.",
                (
                    Option("1", "To hear your balance, press 1."),
                    Option("2", "To make a payment, press 2."),
                    Option("3", "For questions about your bill, press 3.", "billing"),
                ),
            ),
        ),
        Option(
            "2",
            "For technical support, press 2.",
            Menu(
                "Technical support.",
                (
                    Option("1", "For internet, press 1.", "internet"),
                    Option("2", "For TV, press 2."),
                    Option("3", "To check on a technician appointment, press 3.", "visit"),
                ),
            ),
        ),
        Option(
            "3",
            "For mobile, press 3.",
            Menu(
                "Mobile.",
                (
                    Option("1", "To activate a new device, press 1."),
                    Option("2", "For roaming and travel, press 2."),
                    Option("3", "For all other mobile questions, press 3.", "mobile"),
                ),
            ),
        ),
        Option(
            "4",
            "To move or add a service, press 4.",
            Menu(
                "Moving and new service.",
                (
                    Option("1", "To move your service to a new address, press 1.", "moving"),
                    Option("2", "To add a new service, press 2.", "moving"),
                ),
            ),
        ),
        Option(
            "5",
            "For all other inquiries, press 5.",
            Menu(
                "Other inquiries.",
                (
                    Option("1", "To change or cancel your plan, press 1.", "cancel"),
                    Option("9", "To return to the main menu, press 9."),
                ),
            ),
        ),
    ),
)


def speak_seconds(text: str) -> float:
    return len(text.split()) / WORDS_PER_SECOND


def _path(menu: Menu, department: str) -> list[tuple[Menu, int]] | None:
    for index, option in enumerate(menu.options):
        if option.to == department:
            return [(menu, index)]
        if isinstance(option.to, Menu):
            rest = _path(option.to, department)
            if rest:
                return [(menu, index), *rest]
    return None


def _menu_steps(menu: Menu, upto: int | None, at: float) -> tuple[list[dict], float]:
    """The lines a caller hears in one menu, stopping after option `upto`."""
    steps = []
    lines = [menu.intro, *(o.line for o in menu.options[: None if upto is None else upto + 1])]
    for text in lines:
        seconds = speak_seconds(text)
        steps.append({"kind": "say", "text": text, "at": round(at, 1), "dur": round(seconds, 1)})
        at += seconds + PAUSE_SECONDS
    return steps, at


def menu_path(department: str | None) -> dict:
    """The fastest possible trip through the menu to a department.

    The caller is perfect: they know the right branch, press each key the
    moment its line finishes, and never pick wrong. Real callers are slower.
    With no department yet, it returns the main menu as it is read.
    """
    if department is None:
        steps, _ = _menu_steps(MENU, None, 0.0)
        return {"department": None, "steps": steps, "keys": [], "total": None}
    path = _path(MENU, department)
    if not path:
        raise ValueError(f"No menu path to {department}")
    steps, at, keys = [], 0.0, []
    for menu, index in path:
        heard, at = _menu_steps(menu, index, at)
        steps.extend(heard)
        key = menu.options[index].key
        at = at - PAUSE_SECONDS
        steps.append({"kind": "press", "text": key, "at": round(at, 1), "dur": KEY_SECONDS})
        at += KEY_SECONDS
        keys.append(key)
    return {"department": department, "steps": steps, "keys": keys, "total": round(at, 1)}


def router_prompt() -> str:
    lines = "\n".join(f"{d['letter']}) {d['label']}: {d['covers']}" for d in DEPARTMENTS.values())
    return (
        f"You route phone calls for {BRAND}, a telecom company. Read the call so far "
        "and answer with the single letter of the queue the caller needs.\n"
        f"{lines}\n"
        f"{UNCLEAR_LETTER}) Unclear: no request yet, a greeting, asking for a person "
        "without saying why, or nothing to do with these queues.\n"
        "Answer with one letter only."
    )


def read_logprobs(top_logprobs: list[tuple[str, float]]) -> dict[str, float]:
    """Turn the first token's top logprobs into a probability per queue.

    Tokens like "A", " A" and "a" count for the same queue. Probability left on
    tokens that are not a queue letter is not redistributed, so a confused
    model shows up as low confidence instead of false certainty.
    """
    probs = {key: 0.0 for key in [*DEPARTMENTS, UNCLEAR]}
    for token, logprob in top_logprobs:
        letter = token.strip().upper()
        if len(letter) == 1 and letter in LETTERS and math.isfinite(logprob):
            probs[LETTERS[letter]] += math.exp(logprob)
    return {key: min(1.0, value) for key, value in probs.items()}


def decide(probs: dict[str, float], clarified: int) -> dict:
    """Route, clarify, or keep listening. Pure policy: no model involved."""
    ranked = sorted(DEPARTMENTS, key=lambda key: probs.get(key, 0.0), reverse=True)
    top, second = ranked[0], ranked[1]
    confidence = probs.get(top, 0.0)
    if probs.get(UNCLEAR, 0.0) > confidence:
        return {"action": "listen", "target": None, "between": None, "confidence": confidence}
    if confidence >= ROUTE_AT:
        return {"action": "route", "target": top, "between": None, "confidence": confidence}
    if clarified >= MAX_CLARIFY:
        # Two questions is the limit. A best guess beats a third question.
        return {
            "action": "route",
            "target": top,
            "between": None,
            "confidence": confidence,
            "forced": True,
        }
    return {"action": "clarify", "target": None, "between": [top, second], "confidence": confidence}


def instruction(decision: dict) -> str:
    """What the router tells the model to do this turn."""
    label = lambda key: DEPARTMENTS[key]["label"]  # noqa: E731
    pct = round(decision["confidence"] * 100)
    if decision["action"] == "route":
        note = (
            " It is a best guess after two questions, so say so briefly."
            if decision.get("forced")
            else ""
        )
        return (
            f"Router: send this caller to {label(decision['target'])} ({pct}%).{note} "
            "Call transfer now with a one-sentence summary of what they need."
        )
    if decision["action"] == "clarify":
        a, b = decision["between"]
        return (
            f"Router: unsure between {label(a)} and {label(b)}. Ask one short question "
            "that tells these two apart. Do not call transfer and do not list departments."
        )
    if decision.get("error"):
        return "Router: unavailable this turn. Ask the caller to say again what they need."
    return (
        "Router: no clear request yet. Ask in a few words what they are calling about. "
        "Do not call transfer."
    )


def initial_state(now: float | None = None) -> dict:
    return {
        "round": 1,
        "started": time.time() if now is None else now,
        "routed": None,
        "lines": [],
        "probs": None,
        "decision": None,
        "ms": None,
        "clarified": 0,
        "turns": [],
        "handoff": None,
        "history": [],
    }


def new_round(state: dict, now: float) -> None:
    """The caller has a new request after a transfer: both clocks restart."""
    state.update(
        round=state["round"] + 1,
        started=now,
        routed=None,
        lines=[],
        probs=None,
        decision=None,
        ms=None,
        clarified=0,
        turns=[],
        handoff=None,
    )


def hear(state: dict, who: str, text: str) -> None:
    text = " ".join(text.split())[:300]
    if text:
        state["lines"] = [*state["lines"], (who, text)][-8:]


def transcript(state: dict) -> str:
    """The round so far, as the classifier sees it."""
    return "\n".join(
        f"{'Caller' if who == 'caller' else 'Agent'}: {text}" for who, text in state["lines"]
    )[-1500:]


def record(state: dict, heard: str, probs: dict[str, float], ms: int) -> dict:
    decision = decide(probs, state["clarified"])
    if decision["action"] == "clarify":
        state["clarified"] += 1
    state.update(probs=probs, decision=decision, ms=ms)
    state["turns"] = [
        *state["turns"],
        {
            "heard": heard[:160],
            "action": decision["action"],
            "top": decision["target"] or (decision["between"] or [None])[0],
            "confidence": round(decision["confidence"] * 100),
        },
    ][-4:]
    return decision


def record_error(state: dict, heard: str) -> dict:
    decision = {
        "action": "listen",
        "target": None,
        "between": None,
        "confidence": 0.0,
        "error": True,
    }
    state.update(decision=decision, ms=None)
    state["turns"] = [
        *state["turns"],
        {"heard": heard[:160], "action": "error", "top": None, "confidence": 0},
    ][-4:]
    return decision


def transfer(state: dict, summary: str, now: float) -> str:
    """Hand the call to the queue the router chose, never one the model names."""
    decision = state["decision"]
    if state["routed"] is not None:
        return "Already transferred. Ask if there is anything else."
    if not decision or decision["action"] != "route":
        return "Not transferred: the router has not chosen a queue. Follow the router note."
    target = decision["target"]
    summary = " ".join(summary.split())[:200] or "No summary given."
    seconds = round(now - state["started"], 1)
    menu = menu_path(target)
    state["routed"] = now
    state["handoff"] = {
        "department": target,
        "summary": summary,
        "confidence": round(decision["confidence"] * 100),
        "forced": bool(decision.get("forced")),
        "seconds": seconds,
    }
    state["history"] = [
        *state["history"],
        {"department": target, "agent": seconds, "menu": menu["total"], "keys": len(menu["keys"])},
    ][-3:]
    label = DEPARTMENTS[target]["label"]
    return (
        f"Transferred to {label}, with your summary. In one sentence tell the caller they are "
        f"being connected to {label} and will not need to repeat themselves. This is a demo, "
        "so then ask if they want to try another request."
    )


def snapshot(state: dict, now: float | None = None) -> dict:
    """Everything the screen shows, as one event. Probabilities in whole percent."""
    now = time.time() if now is None else now
    probs = state["probs"] or {}
    decision = state["decision"]
    handoff = state["handoff"]
    return {
        "brand": BRAND,
        "round": state["round"],
        "elapsedMs": max(0, int((now - state["started"]) * 1000)),
        "routeAt": round(ROUTE_AT * 100),
        "departments": [{"id": key, "label": d["label"]} for key, d in DEPARTMENTS.items()],
        "scores": [
            {"id": key, "p": round(probs.get(key, 0.0) * 100)} for key in [*DEPARTMENTS, UNCLEAR]
        ]
        if state["probs"]
        else [],
        "decision": None
        if not decision
        else {
            "action": decision["action"],
            "target": decision["target"],
            "between": decision["between"],
            "confidence": round(decision["confidence"] * 100),
            "ms": state["ms"],
        },
        "clarified": state["clarified"],
        "turns": state["turns"],
        "handoff": handoff,
        "menu": menu_path(handoff["department"] if handoff else None),
        "history": state["history"],
    }
