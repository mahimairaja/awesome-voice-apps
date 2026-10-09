"""Maintainer-owned adapter for the standalone panel scribe demo.

The contributed demo labels several voices with pyannoteAI Live. A playground
call is one visitor and the agent, so the hosted version lets the visitor play
the whole panel: they say which interviewer is talking ("engineer here") and
the scribe attributes every line after that to that seat until the next
handoff. Everything else (silent listening, the transcript, talk time, the
"scribe, recap" scorecard) is the contributed agent's own code.
"""

import importlib.util
import re
import sys
import types
from pathlib import Path
from typing import Literal

from livekit.agents import RunContext, StopResponse, function_tool
from pydantic import BaseModel

# The demo imports its Cerebras and Rime plugins at module level. The hosted
# worker swaps to the metered Deepgram, OpenAI and Cartesia stack, so give those
# imports inert placeholders for the load only.
_PROVIDERS = ("cerebras", "rime")


def _load():
    demo = Path(__file__).resolve().parents[1] / "panel-scribe"
    spec = importlib.util.spec_from_file_location("playground_panel_agent", demo / "agent.py")
    module = importlib.util.module_from_spec(spec)
    added = []
    for name in _PROVIDERS:
        key = f"livekit.plugins.{name}"
        if key not in sys.modules:
            sys.modules[key] = types.ModuleType(key)
            added.append(key)
    try:
        spec.loader.exec_module(module)
    finally:
        for key in added:
            sys.modules.pop(key, None)
    return module


_module = _load()
PanelScribe = _module.PanelScribe

SEATS = ("Hiring manager", "Engineer", "Recruiter")
_ALIASES = {
    "hiring manager": "Hiring manager",
    "manager": "Hiring manager",
    "engineer": "Engineer",
    "recruiter": "Recruiter",
}
# A handoff opens the turn: "Engineer here.", "This is the recruiter, ...",
# "Switching to the hiring manager." Names later in a sentence are just talk.
_HANDOFF = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|now|so)[\s,]+)?"
    r"(?:(?:this is|it's|it is|switch(?:ing)? to|speaking as|as|over to)\s+)?"
    r"(?:the\s+)?(hiring manager|manager|engineer|recruiter)\b"
    r"(?:\s+(?:here|speaking))?[\s,.:;!-]*",
    re.IGNORECASE,
)

HOSTED_INSTRUCTIONS = (
    " In this public demo one visitor plays the whole panel. Each line is "
    "prefixed with the seat that said it: Hiring manager, Engineer or Recruiter. "
    "Only give scorecard rows for seats that spoke."
)

GREETING = (
    "In two short sentences: say this is a hiring-panel simulation where they play "
    "every interviewer, so they should start each part with hiring manager, engineer "
    "or recruiter, and say scribe recap for the scorecard. Then stop."
)


def handoff(text: str) -> tuple[str | None, str]:
    """Split a spoken seat handoff from the rest of the turn."""
    match = _HANDOFF.match(text)
    if not match:
        return None, text
    return _ALIASES[match.group(1).lower()], text[match.end() :].strip()


class SpokenPanel:
    """Stands in for the pyannote sidecar: the visitor names the seat instead.

    Exposes the attributes PanelScribe reads (current_speaker, display_speaker,
    talk_time_items, feed_frame). Talk time is each seat's share of words.
    """

    def __init__(self) -> None:
        self.current_speaker: str = SEATS[0]
        self.words: dict[str, int] = {}

    def feed_frame(self, frame) -> None:
        pass

    def display_speaker(self) -> str:
        return self.current_speaker

    def record(self, text: str) -> None:
        count = len(text.split())
        if count:
            self.words[self.current_speaker] = self.words.get(self.current_speaker, 0) + count

    def talk_time_items(self, now: float = 0.0) -> list[dict]:
        total = sum(self.words.values())
        if not total:
            return []
        top = max(self.words, key=self.words.get)
        return [
            {"label": seat, "value": round(self.words[seat] / total, 3), "driver": seat == top}
            for seat in SEATS
            if seat in self.words
        ]


class ScorecardRow(BaseModel):
    interviewer: Literal["Hiring manager", "Engineer", "Recruiter"]
    strengths: str
    concerns: str
    lean: Literal["strong hire", "hire", "no hire", "strong no hire", "undecided"]


class SoloPanelScribe(PanelScribe):
    def __init__(self, room) -> None:
        super().__init__(room, SpokenPanel())
        self._instructions = _module.INSTRUCTIONS + HOSTED_INSTRUCTIONS

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        seat, rest = handoff((new_message.text_content or "").strip())
        if seat:
            self.sidecar.current_speaker = seat
            if not rest:
                # A bare "engineer here" only changes the seat.
                raise StopResponse()
            new_message.content = [rest]
        self.sidecar.record(new_message.text_content or "")
        await super().on_user_turn_completed(turn_ctx, new_message)

    # Same tool as the contributed demo, with typed rows: OpenAI's strict tool
    # schema rejects the original free-form dicts.
    @function_tool()
    async def publish_scorecard(
        self, context: RunContext, rows: list[ScorecardRow], consensus: str
    ) -> str:
        """Render the debrief scorecard on screen.

        rows: one row per interviewer seat that spoke. consensus: one sentence
        overall hiring lean.
        """
        _module.publish_scorecard_ui(self.room, [row.model_dump() for row in rows], consensus)
        return "scorecard published"


def publish_panel(room) -> None:
    _module.publish_transcript(room, [])
    _module.publish_talk_time(room, [])
