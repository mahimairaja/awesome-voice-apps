"""The voice metrics stored for each call.

DeepEval's seven local voice metrics score the call audio. Two of them,
Voice Naturalness and Speech Intelligibility, are signal heuristics (level,
noise floor, short gaps), so they mostly measure how loud the agent's audio
arrives: clean Kokoro speech scores 0.83 for intelligibility offline, while
every hosted demo scored 0.35 on 2026-10-20. They are stored as information
(see INFORMATIONAL in report.py) until a run with saved audio shows them
tracking what a listener hears.

Two word-level checks replace them as the measure of understanding. Both run
locally, need no paid model and compare words, not audio statistics:

- ``TranscriptionAccuracyMetric``: did the agent hear the caller? The caller
  says known script lines, so the agent's own transcript of the caller is
  compared with them (1 minus word error rate).
- ``AgentIntelligibilityMetric``: does the agent's audio say what it meant?
  Its speech is transcribed again with faster-whisper and compared with the
  text the agent published.

Goal, relevance, tool use and policy are judged from the transcript by Claude
(see judge_claude.py and JUDGE.md).
"""

from __future__ import annotations

import math
import re

import jiwer
from deepeval.metrics import (
    AgentResponsivenessMetric,
    AudioIntegrityMetric,
    SpeechIntelligibilityMetric,
    VoiceConsistencyMetric,
    VoiceNaturalnessMetric,
    VoiceReliabilityMetric,
)
from deepeval.metrics import TurnTakingNaturalnessMetric as DeepEvalTurnTaking
from deepeval.metrics.voice.base_metric import BaseVoiceMetric
from deepeval.voice.timeline import build_audio_timeline
from report import INFORMATIONAL, metric_key

DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def normalize(text: str) -> str:
    """Lowercase words with digits spelled out, so '4471' matches 'four four seven one'."""
    text = re.sub(r"\d", lambda m: f" {DIGITS[int(m.group())]} ", text.lower())
    text = text.replace("&", " and ").replace("%", " percent ")
    return " ".join(re.sub(r"[^a-z' ]+", " ", text).replace("'", "").split())


def word_accuracy(reference: str, hypothesis: str) -> float | None:
    """1 minus the word error rate, floored at 0. None when there is no reference."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return None
    if not hyp:
        return 0.0
    return max(0.0, 1.0 - jiwer.wer(ref, hyp))


class TranscriptionAccuracyMetric(BaseVoiceMetric):
    """How much of the scripted caller the agent's speech to text got right."""

    def __init__(self, said: list[str], heard: list[str], threshold: float = 0.85):
        super().__init__(threshold=threshold)
        self.said = said
        self.heard = heard

    def _evaluate(self, test_case):
        said, heard = " ".join(self.said), " ".join(self.heard)
        score = word_accuracy(said, heard)
        if score is None:
            return None, "The caller said nothing.", {}
        if not self.heard:
            return 0.0, "The agent published no transcript of the caller.", {}
        return (
            score,
            (
                f"The agent heard {score:.0%} of the caller's words right"
                f" ({len(self.heard)} transcript(s) for {len(self.said)} line(s))."
            ),
            {"said": said[:400], "heard": heard[:400]},
        )

    @property
    def __name__(self):
        return "Transcription Accuracy"


class AgentIntelligibilityMetric(BaseVoiceMetric):
    """How closely a speech-to-text pass on the agent's audio matches its text."""

    def __init__(self, stt, threshold: float = 0.85):
        super().__init__(threshold=threshold)
        self.stt = stt

    def _evaluate(self, test_case):
        scored, worst = [], None
        for index, turn in enumerate(test_case.turns):
            if turn.role != "assistant" or turn.audio is None or not (turn.content or "").strip():
                continue
            spoken, _ = self.stt.transcribe(turn.audio)
            score = word_accuracy(turn.content, spoken)
            if score is None:
                continue
            scored.append(score)
            if worst is None or score < worst[0]:
                worst = (score, index, turn.content, spoken)
        if not scored:
            return None, "No agent turn had both audio and text.", {}
        score = sum(scored) / len(scored)
        reason = (
            f"Speech to text recovered {score:.0%} of the agent's words over {len(scored)} turn(s)."
        )
        if worst and worst[0] < 0.85:
            reason += f' Worst turn said "{worst[2][:80]}" but was heard as "{worst[3][:80]}".'
        return score, reason, {"turns": len(scored)}

    @property
    def __name__(self):
        return "Agent Intelligibility"


class TurnTakingNaturalnessMetric(DeepEvalTurnTaking):
    """DeepEval's turn-taking score over the agent's side of each exchange only.

    DeepEval scores every change of speaker. The scripted caller waits until the
    agent has been quiet for 1.5 s (see AgentConnector.wait_until_quiet) and then
    synthesizes its line, so each agent-to-caller gap is at least 1.5 s and scored
    at most exp(-1.5 / 2.5) = 0.55 whatever the agent does. Here only the
    transitions into an agent turn count: the wait after the caller stops, or the
    agent talking over the caller. Same scoring curves as DeepEval, and the same
    key, so stored results stay comparable.
    """

    def _evaluate(self, test_case):
        result = super()._evaluate(test_case)
        if result[0] is None:
            return result
        timeline = build_audio_timeline(test_case.turns)
        transitions, scores = [], []
        for current, following in zip(timeline, timeline[1:]):
            if current.role == following.role or following.role != "assistant":
                continue
            gap = following.start_time - current.end_time
            score = math.exp(-gap / 2.5) if gap >= 0 else math.exp(-abs(gap) / 0.6)
            scores.append(score)
            transitions.append(
                {
                    "from_turn": current.turn_index,
                    "to_turn": following.turn_index,
                    "kind": "gap" if gap >= 0 else "agent_overlap",
                    "gap_seconds": gap,
                    "score": score,
                }
            )
        if not scores:
            return None, "No caller-to-agent transitions to evaluate.", {"transitions": []}
        score = sum(scores) / len(scores)
        return (
            score,
            f"Turn-taking naturalness was {score:.2f} across {len(scores)} caller-to-agent "
            "transition(s); the scripted caller's own wait is not scored.",
            {"transitions": transitions},
        )


def voice_metrics():
    """Fresh metric instances: a metric keeps its last score on itself."""
    return [
        VoiceNaturalnessMetric(),
        SpeechIntelligibilityMetric(),
        VoiceConsistencyMetric(),
        AudioIntegrityMetric(),
        TurnTakingNaturalnessMetric(),
        AgentResponsivenessMetric(),
        VoiceReliabilityMetric(),
    ]


def word_metrics(said: list[str], heard: list[str], stt):
    """Word-level metrics for one call.

    ``said`` is the caller's script, ``heard`` the agent's transcripts of the
    caller, and ``stt`` the whisper model used to transcribe the agent's audio.
    Returns a TranscriptionAccuracyMetric and an AgentIntelligibilityMetric.
    """
    return [TranscriptionAccuracyMetric(said, heard), AgentIntelligibilityMetric(stt)]


def gating_metrics():
    """The DeepEval metrics a call must pass (see INFORMATIONAL in report.py)."""
    return [m for m in voice_metrics() if metric_key(m) not in INFORMATIONAL]


VOICE_METRICS = voice_metrics()
