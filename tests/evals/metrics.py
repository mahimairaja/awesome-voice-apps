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

A silent assistant clip (the agent's transcript arrived but its audio did
not) is not the agent's voice, so the audio metrics leave it out and name it
in their reason instead of scoring it as a very quiet turn (see AudibleTurns).

Goal, relevance, tool use and policy are judged from the transcript by Claude
(see judge_claude.py and JUDGE.md).
"""

from __future__ import annotations

import copy
import math
import re

import jiwer
import numpy as np
from deepeval import metrics as deepeval_metrics
from deepeval.metrics import (
    AgentResponsivenessMetric,
    AudioIntegrityMetric,
    VoiceReliabilityMetric,
)
from deepeval.metrics import TurnTakingNaturalnessMetric as DeepEvalTurnTaking
from deepeval.metrics.voice.base_metric import BaseVoiceMetric
from deepeval.test_case import Audio
from deepeval.voice.connectors import audio_utils
from deepeval.voice.timeline import build_audio_timeline
from report import INFORMATIONAL, metric_key
from speech import wav_samples

DIGITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


# A turn whose whole clip is quieter than this carries no speech. The phantom
# last turn of a call hung up too early sat near -107 dBFS; live agent speech
# arrives between -18 and -33 dBFS.
SILENT_DBFS = -60.0


def is_silent(audio) -> bool:
    """True when the clip is digital silence. Undecodable audio is not judged here."""
    try:
        samples, _ = wav_samples(audio)
    except Exception:  # noqa: BLE001 (not WAV: leave it to the metric)
        return False
    if not len(samples):
        return True
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return rms <= 10 ** (SILENT_DBFS / 20)


def silent_turns(turns) -> list[int]:
    """Indexes of assistant turns whose audio is silent."""
    return [
        index
        for index, turn in enumerate(turns)
        if turn.role == "assistant" and turn.audio is not None and is_silent(turn.audio)
    ]


def no_audio_note(indexes: list[int]) -> str:
    return f"No audio in turn(s) {', '.join(map(str, indexes))}; left out."


class AudibleTurns:
    """Scores a call as if its silent assistant turns carried no audio.

    A silent clip is not the agent's voice, so it must not count as a quiet
    turn (Voice Consistency weighs loudness spread) or as a turn on the
    timeline. Each silent turn is named in the reason and in
    ``no_audio_turns`` instead.
    """

    def _evaluate(self, test_case):
        silent = silent_turns(test_case.turns)
        if not silent:
            return super()._evaluate(test_case)
        turns = list(test_case.turns)
        for index in silent:
            turns[index] = copy.copy(turns[index])
            turns[index].audio = None
        muted = copy.copy(test_case)
        muted.turns = turns
        score, reason, breakdown = super()._evaluate(muted)
        reason = f"{reason or ''} {no_audio_note(silent)}".strip()
        return score, reason, {**(breakdown or {}), "no_audio_turns": silent}


def skips_silent(metric_class, name: str | None = None):
    """``metric_class`` with AudibleTurns in front, under the same class name."""
    name = name or metric_class.__name__
    return type(name, (AudibleTurns, metric_class), {"__module__": __name__})


VoiceNaturalnessMetric = skips_silent(deepeval_metrics.VoiceNaturalnessMetric)
SpeechIntelligibilityMetric = skips_silent(deepeval_metrics.SpeechIntelligibilityMetric)
VoiceConsistencyMetric = skips_silent(deepeval_metrics.VoiceConsistencyMetric)


def normalize(text: str) -> str:
    """Lowercase words with digits spelled out, so '4471' matches 'four four seven one'."""
    text = text.replace("\u2019", "'")  # a curly apostrophe keeps "that's" one word
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


def replies(turns) -> list[list[int]]:
    """Indexes of each run of assistant turns between two caller turns.

    A LiveKit agent publishes one reply as several transcript segments, while
    the harness cuts its audio on silence, so a segment's text and audio rarely
    cover the same words. The run as a whole does.
    """
    runs, run = [], []
    for index, turn in enumerate(turns):
        if turn.role == "assistant":
            run.append(index)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    return runs


def joined_audio(turns) -> Audio | None:
    """The turns' clips end to end, in turn order, or None when none has audio.

    Not placed by start time: DeepEval dates a clip by its first voiced frame
    but keeps the silence before it, so a timeline would lay one segment's
    speech over the next. The downlink is read in order, so the clips follow
    each other without overlap.
    """
    clips = [turn.audio for turn in turns if turn.audio is not None]
    if len(clips) <= 1:
        return clips[0] if clips else None
    rate, joined = None, bytearray()
    for audio in clips:
        pcm, own_rate, channels = audio_utils.wav_bytes_to_pcm16(audio.get_bytes())
        pcm = audio_utils.downmix_to_mono(pcm, channels or 1)
        rate = rate or own_rate
        joined += pcm if own_rate == rate else audio_utils.resample_pcm16(pcm, own_rate, rate)
    return Audio.from_bytes(audio_utils.pcm16_to_wav_bytes(bytes(joined), rate, 1), "audio/wav")


class AgentIntelligibilityMetric(BaseVoiceMetric):
    """How closely a speech-to-text pass on the agent's audio matches its text.

    Scored per reply: the segments between two caller turns are joined, text
    and audio, before comparing. Silent segments are left out and named.
    """

    def __init__(self, stt, threshold: float = 0.85):
        super().__init__(threshold=threshold)
        self.stt = stt

    def _evaluate(self, test_case):
        turns = test_case.turns
        silent = silent_turns(turns)
        scored, worst = [], None
        for run in replies(turns):
            kept = [turns[i] for i in run if i not in silent]
            text = " ".join((t.content or "").strip() for t in kept).strip()
            audio = joined_audio(kept)
            if audio is None or not text:
                continue
            spoken, _ = self.stt.transcribe(audio)
            score = word_accuracy(text, spoken)
            if score is None:
                continue
            scored.append(score)
            if worst is None or score < worst[0]:
                worst = (score, run[0], text, spoken)
        extra = {"no_audio_turns": silent} if silent else {}
        note = f" {no_audio_note(silent)}" if silent else ""
        if not scored:
            return None, f"No agent reply had both audio and text.{note}", extra
        score = sum(scored) / len(scored)
        reason = (
            f"Speech to text recovered {score:.0%} of the agent's words"
            f" over {len(scored)} reply(ies)."
        )
        if worst and worst[0] < 0.85:
            reason += f' Worst reply said "{worst[2][:80]}" but was heard as "{worst[3][:80]}".'
        return score, reason + note, {"replies": len(scored), **extra}

    @property
    def __name__(self):
        return "Agent Intelligibility"


class _AgentSideTurnTaking(DeepEvalTurnTaking):
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


TurnTakingNaturalnessMetric = skips_silent(_AgentSideTurnTaking, "TurnTakingNaturalnessMetric")


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
