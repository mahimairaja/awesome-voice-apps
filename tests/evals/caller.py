"""The scripted caller: fixed lines, no LLM, so every run says the same thing.

Each golden carries its caller lines in ``additional_metadata["script"]``. A
line may read a value off the demo's screen, the way a visitor reads the
bottle label: ``{Refill.label.rx|spell}`` is the ``rx`` field of the last
``Refill`` UI event the agent published, spelled out character by character.
"""

from __future__ import annotations

import json
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
