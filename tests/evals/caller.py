"""The scripted caller: fixed lines, no LLM, so every run says the same thing.

Each golden carries its caller lines in ``additional_metadata["script"]``. A
line may read a value off the demo's screen, the way a visitor reads the
bottle label: ``{Refill.label.rx|spell}`` is the ``rx`` field of the last
``Refill`` UI event the agent published, spelled out character by character.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date

from deepeval.dataset import ConversationalGolden
from deepeval.simulator.controller import end, proceed
from deepeval.simulator.simulation_graph import SimulationNode
from deepeval.test_case import Turn

PLACEHOLDER = re.compile(r"\{([A-Za-z][\w.]*)(?:\|(\w+))?\}")
DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


class UiState:
    """The latest props of each UI component, read from the room's ``ui`` topic."""

    def __init__(self) -> None:
        self.components: dict[str, dict] = {}

    def attach(self, room) -> None:
        room.on("data_received", self.on_data)

    def on_data(self, packet) -> None:
        if packet.topic != "ui":
            return
        try:
            event = json.loads(packet.data)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(event, dict) or not isinstance(event.get("props"), dict):
            return
        component = str(event.get("component", ""))
        if event.get("action") == "unmount":
            self.components.pop(component, None)
        else:
            self.components.setdefault(component, {}).update(event["props"])

    def lookup(self, path: str):
        value: object = self.components
        for key in path.split("."):
            if not isinstance(value, dict) or key not in value:
                raise KeyError(path)
            value = value[key]
        return value


def spell(value: str) -> str:
    """'4471-B' -> 'four four seven one, B': codes read one character at a time."""
    parts = []
    for char in str(value).upper():
        if char.isdigit():
            parts.append(DIGITS[int(char)])
        elif char.isalpha():
            parts.append(char)
        elif parts and parts[-1] != ",":
            parts.append(",")
    return " ".join(parts).replace(" ,", ",").strip(", ")


def spoken_date(value: str) -> str:
    """'1984-03-12' -> 'March 12, 1984'."""
    day = date.fromisoformat(str(value))
    return f"{day:%B} {day.day}, {day.year}"


FILTERS = {"spell": spell, "date": spoken_date, "lower": lambda v: str(v).lower()}


def render(line: str, ui: UiState) -> str:
    def swap(match: re.Match) -> str:
        value = ui.lookup(match.group(1))
        name = match.group(2)
        return FILTERS[name](value) if name else str(value)

    return PLACEHOLDER.sub(swap, line)


def script(golden: ConversationalGolden) -> list[str]:
    lines = (golden.additional_metadata or {}).get("script")
    if not isinstance(lines, list) or not lines or not all(isinstance(x, str) for x in lines):
        raise ValueError(f"Golden {golden.name!r} has no caller script.")
    return lines


# How long a script takes to say, from the 2026-11-02 sweep of 36 calls. Each
# line costs the agent's reply (about 2.5 s to first audio, then 6 to 10 s of
# speech), the caller's 1.5 s wait for quiet, and the line itself at about 2.5
# words a second. The base covers the greeting and the last reply. Lines took
# 15 s at the median and 24 s at the slowest.
CALL_BASE_S = 20
LINE_S = 18
WORD_S = 0.4


def script_seconds(lines: list[str]) -> int:
    """Seconds a call needs to say ``lines``, rounded up to 5 and not capped.

    A golden's ``seconds`` is at least this, or the demo's cap on the site
    when the script needs more (see test_every_call_fits_its_script).
    """
    need = CALL_BASE_S + sum(LINE_S + WORD_S * len(line.split()) for line in lines)
    return 5 * math.ceil(need / 5)


# Demos no run calls, site or cloud, with the reason the test report shows.
# Their goldens stay in .dataset.json for when they can run again.
SKIPPED_DEMOS = {
    "concierge": (
        "The playground cannot start concierge without Spatius avatar keys,"
        " so no agent ever joins the eval call."
    ),
}


def skip_reason(golden: ConversationalGolden) -> str | None:
    """Why the golden's demo is left out of runs, or None when it runs."""
    return SKIPPED_DEMOS.get((golden.additional_metadata or {}).get("demo"))


def caller_graph(ui: UiState) -> SimulationNode:
    """One node, no edges: the runner never asks an LLM which way to go."""

    def next_line(turns: list[Turn], golden: ConversationalGolden) -> str:
        spoken = sum(1 for turn in turns if turn.role == "user")
        return render(script(golden)[spoken], ui)

    return SimulationNode(action=next_line, name="scripted_caller")


def stop_after_script(golden: ConversationalGolden, simulated_user_turns: int):
    """End once every line was said and the agent answered the last one."""
    if simulated_user_turns >= len(script(golden)):
        return end("The caller finished the script.")
    return proceed()


# How long the caller holds on after its last line for the agent to start
# answering, when the exchange that followed the line ended without a voiced
# reply. Replies here start within about 3 s; tool calls take longer.
LAST_REPLY_START_S = 10.0


def voiced(turn) -> bool:
    """True when the turn's audio holds sound above the silence floor."""
    from metrics import is_silent

    return turn.audio is not None and not is_silent(turn.audio)


def patient_simulator():
    """ConversationSimulator that lets the agent finish before the caller speaks.

    DeepEval collects whatever the agent said since its last turn right before
    each caller line. This waits first, while the agent is still talking (see
    ``AgentConnector.wait_until_quiet``), so that collection holds the whole
    reply and the caller never talks over it. After the last line, when the
    call is about to end, it also waits for that reply to start and finish and
    keeps it as a turn. Built lazily so offline tests that never place a call
    do not import the simulator.
    """
    from deepeval.simulator import ConversationSimulator

    class PatientSimulator(ConversationSimulator):
        async def _take_agent_backlog(self, session, turns, call_started_at):
            wait = getattr(session.connector, "wait_until_quiet", None)
            if wait is not None:
                await wait()
            return await super()._take_agent_backlog(session, turns, call_started_at)

        async def _voice_duplex_exchange(self, session, input, turns, golden):
            reply = await super()._voice_duplex_exchange(session, input, turns, golden)
            spoken = sum(1 for turn in turns if turn.role == "user")
            wait = getattr(session.connector, "wait_until_quiet", None)
            if spoken < len(script(golden)) or wait is None or session.connector.call_ended:
                return reply
            # The stopping check hangs up next, so the rest of the reply is
            # taken now. A reply that has not started gets LAST_REPLY_START_S.
            await wait(0.0 if voiced(reply) else LAST_REPLY_START_S)
            before = len(turns)
            started_at = session.started_at or 0.0
            await ConversationSimulator._take_agent_backlog(self, session, turns, started_at)
            return turns[-1] if len(turns) > before else reply

    return PatientSimulator
