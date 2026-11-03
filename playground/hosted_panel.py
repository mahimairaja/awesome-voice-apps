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

from livekit.agents import RunContext, StopResponse, ToolError, function_tool
from pydantic import BaseModel

# The demo imports its Cerebras and Rime plugins at module level. The hosted
# worker swaps to the metered Deepgram, OpenAI and Cartesia stack, so give those
# imports inert placeholders for the load only.
_PROVIDERS = ("cerebras", "rime")


def _load():
    demo = Path(__file__).resolve().parents[1] / "demos" / "panel-scribe"
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
# "I'm the hiring manager." A bare seat name counts only when a pause or
# "here"/"speaking" follows it, so "Manager expects stronger tests" stays a line.
_SEAT = r"(?:the\s+)?(?P<{}>hiring manager|manager|engineer|recruiter)"
_HANDOFF = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|now|so)[\s,]+)?"
    r"(?:"
    r"(?:i['\u2019]m|i am|this is|it['\u2019]s|it is|switch(?:ing)? to|speaking as|as|over to)\s+"
    + _SEAT.format("cued")
    + r"\b(?:\s+(?:here|speaking)\b)?"
    r"|" + _SEAT.format("bare") + r"(?:\s+(?:here|speaking)\b|\s*(?=[,.:;!-]|$))"
    r")[\s,.:;!-]*",
    re.IGNORECASE,
)

HOSTED_INSTRUCTIONS = (
    " In this public demo one visitor plays the whole panel. Each line is "
    "prefixed with the seat that said it: Hiring manager, Engineer or Recruiter. "
    "Give a scorecard row for every seat that spoke and none for a seat that did not."
)

# A note says nothing when it is one of these; anything else must echo the seat.
_NONE = {"none", "none stated", "none mentioned", "not stated", "not mentioned", "n/a", "-"}
# Words a model adds to any scorecard, so they never count as echoing a seat.
_FILLER = frozenset(
    "about also candidate concern concerns good great hire into just more really recap "
    "said says scribe some strong that their them they think this very well what with "
    "would".split()
)


def _stems(text: str) -> set[str]:
    """Content words cut to five letters, so mentored matches mentoring."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:5] for w in words if len(w) >= 4 and w not in _FILLER}


def unsupported(note: str, lines: str) -> bool:
    """True when a note shares no content word with what the seat said."""
    note = note.strip()
    if note.lower().rstrip(".") in _NONE:
        return False
    return not (_stems(note) & _stems(lines))


GREETING = (
    "In two short sentences: say this is a hiring-panel simulation where they play "
    "every interviewer, so they should start each part with hiring manager here, "
    "engineer here or recruiter here, and say scribe recap for the scorecard. Then stop."
)


def handoff(text: str) -> tuple[str | None, str]:
    """Split a spoken seat handoff from the rest of the turn."""
    match = _HANDOFF.match(text)
    if not match:
        return None, text
    seat = match.group("cued") or match.group("bare")
    return _ALIASES[seat.lower()], text[match.end() :].strip()


class SpokenPanel:
    """Stands in for the pyannote sidecar: the visitor names the seat instead.

    Exposes the attributes PanelScribe reads (current_speaker, display_speaker,
    talk_time_items, feed_frame). Talk time is each seat's share of words.
    """

    def __init__(self) -> None:
        self.current_speaker: str = SEATS[0]
        self.words: dict[str, int] = {}

    def feed_frame(self, frame) -> None:
        """Ignore audio: the seat comes from the spoken handoff."""

    def display_speaker(self) -> str:
        """Return the seat the visitor last handed off to."""
        return self.current_speaker

    def record(self, text: str) -> None:
        """Credit the line's words to the current seat."""
        count = len(text.split())
        if count:
            self.words[self.current_speaker] = self.words.get(self.current_speaker, 0) + count

    def talk_time_items(self, now: float = 0.0) -> list[dict]:
        """Return each seat's share of words, marking the top talker."""
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
    """One interviewer's debrief row, typed for OpenAI's strict tool schema."""

    interviewer: Literal["Hiring manager", "Engineer", "Recruiter"]
    strengths: str
    concerns: str
    lean: Literal["strong hire", "hire", "no hire", "strong no hire", "undecided"]


class SoloPanelScribe(PanelScribe):
    """PanelScribe for one visitor who speaks every seat in turn."""

    def __init__(self, room) -> None:
        super().__init__(room, SpokenPanel())
        self._instructions = _module.INSTRUCTIONS + HOSTED_INSTRUCTIONS

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        """Apply a leading seat handoff, then score the rest of the line."""
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

        rows: one row for every interviewer seat that spoke, with strengths and
        concerns taken from that seat's own words, or "none stated".
        consensus: one sentence overall hiring lean.
        """
        said: dict[str, str] = {}
        for line in self.transcript:
            said[line["speaker"]] = f"{said.get(line['speaker'], '')} {line['text']}"
        # A seat spoke when it said more than "scribe, recap".
        said = {seat: text for seat, text in said.items() if _stems(text)}
        # Keep only seats the visitor actually spoke as.
        spoken = {row.interviewer: row for row in rows if row.interviewer in said}
        problems = [
            f"add a row for {seat}" for seat in SEATS if seat in said and seat not in spoken
        ]
        for seat, row in spoken.items():
            for field in ("strengths", "concerns"):
                if unsupported(getattr(row, field), said[seat]):
                    problems.append(f"{seat} {field} must use what the {seat} said, or none stated")
        if problems:
            # The model calls again with the fix; nothing half-made reaches the screen.
            raise ToolError("Scorecard not published: " + "; ".join(problems) + ".")
        ordered = [spoken[seat].model_dump() for seat in SEATS if seat in spoken]
        _module.publish_scorecard_ui(self.room, ordered, consensus)
        return "scorecard published"


def publish_panel(room) -> None:
    """Mount the empty transcript and talk-time panels."""
    _module.publish_transcript(room, [])
    _module.publish_talk_time(room, [])
