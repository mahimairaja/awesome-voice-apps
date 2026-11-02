"""Turn a simulated call and its metric results into the site's call record."""

from __future__ import annotations

import re

from deepeval.dataset import ConversationalGolden
from deepeval.test_case import ConversationalTestCase
from deepeval.voice.connectors import audio_utils

# Stored and shown, but never fail a call. DeepEval counts any quiet gap of
# 200 ms or less between voiced frames as a dropout, so the ordinary pauses
# between words in clean TTS (8 to 13 per sentence from Kokoro, offline) read
# as defects, and Voice Reliability averages that score in.
INFORMATIONAL = {"audio_integrity", "voice_reliability"}
# Signal heuristics that mostly track how loud the audio arrives (see
# metrics.py). Stored until saved recordings show they follow what a listener
# hears; the word-level checks carry intelligibility meanwhile.
INFORMATIONAL |= {"voice_naturalness", "speech_intelligibility"}


def metric_key(metric) -> str:
    """'VoiceNaturalnessMetric' -> 'voice_naturalness'."""
    name = type(metric).__name__.removesuffix("Metric")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def events(metric) -> str:
    """'abrupt_cutoff x2, audio_dropout x3': what an audio-integrity score counted."""
    found = (getattr(metric, "score_breakdown", None) or {}).get("events") or []
    counts: dict[str, int] = {}
    for event in found:
        if isinstance(event, dict) and event.get("type"):
            count = event.get("count", 1)
            counts[event["type"]] = counts.get(event["type"], 0) + (
                count if isinstance(count, int) else 1
            )
    return ", ".join(f"{kind} x{n}" for kind, n in counts.items())


def scores(metrics) -> list[dict]:
    out = []
    for metric in metrics:
        score = getattr(metric, "score", None)
        reason = getattr(metric, "reason", None) or getattr(metric, "error", None)
        counted = events(metric)
        if reason and counted:
            reason = f"{reason} ({counted})"
        out.append(
            {
                "metric": metric_key(metric),
                "score": None if score is None else round(min(max(float(score), 0.0), 1.0), 4),
                "passed": None
                if score is None or metric_key(metric) in INFORMATIONAL
                else bool(getattr(metric, "success", False)),
                "reason": str(reason)[:400] if reason else None,
            }
        )
    # The site rejects a null passed flag; drop the key instead.
    return [{k: v for k, v in s.items() if v is not None or k == "score"} for s in out]


# A pause in a caller's line long enough for an agent to take the turn: the
# hosted demos end a turn after 0.3 to 1.2 s of quiet. A reply that starts
# more than PAUSE_REPLY_MAX_S after such a pause is not an answer to it.
MIN_PAUSE_S = 0.5
PAUSE_REPLY_MAX_S = 5.0


def last_pause(audio, at_s: float) -> float | None:
    """When the clip's last pause of MIN_PAUSE_S or more before ``at_s`` began.

    In call time; None when the clip has no start time or no such pause.
    Uses DeepEval's own 20 ms frame silence floor, the one it ends turns on.
    """
    start = getattr(audio, "start_time", None)
    if start is None or at_s <= start:
        return None
    pcm, rate, channels = audio_utils.wav_bytes_to_pcm16(audio.get_bytes())
    pcm = audio_utils.downmix_to_mono(pcm, channels or 1)
    step = 2 * int(rate * audio_utils.DEFAULT_FRAME_MS / 1000)
    frame_s = audio_utils.DEFAULT_FRAME_MS / 1000
    stop = min(len(pcm), 2 * int((at_s - start) * rate))
    found, quiet_from = None, None
    for index, offset in enumerate(range(0, stop - step + 1, step)):
        if audio_utils.is_silent(pcm[offset : offset + step]):
            quiet_from = index if quiet_from is None else quiet_from
        else:
            quiet_from = None
        if quiet_from is not None and (index + 1 - quiet_from) * frame_s >= MIN_PAUSE_S:
            found = start + quiet_from * frame_s
    return found


def pause_latency_ms(caller, reply) -> float | None:
    """The wait before a reply that the agent began in a pause in the caller's line.

    DeepEval times a reply from the end of the caller's clip and floors one
    that began earlier at 0. The interview demo took the turn in a pause of
    every long line on 2026-11-02 (its first reply came 1.5 s into a 1.5 s
    pause, just as the caller went on), so the call stored no latency at all.
    Timed from the start of that pause, the reply has a real wait. None when
    there was no such pause shortly before it (the agent talked over the
    caller) or the clips carry no times.
    """
    if caller is None or caller.audio is None or reply.audio is None:
        return None
    started = reply.audio.start_time
    if started is None:
        return None
    paused = last_pause(caller.audio, started)
    if paused is None or started - paused > PAUSE_REPLY_MAX_S:
        return None
    return (started - paused) * 1000.0


def turns(case: ConversationalTestCase | None) -> list[dict]:
    out, caller = [], None
    for turn in (case.turns if case else [])[:80]:
        # The agent's own words where whisper misread a clip (metrics.clean_call).
        text = (getattr(turn, "metadata", None) or {}).get("agent_words") or turn.content or ""
        item = {"role": turn.role, "text": text[:2000]}
        if turn.audio is not None and turn.audio.start_time is not None:
            item["start_s"] = round(turn.audio.start_time, 3)
        # DeepEval floors a reply that began before the caller finished at 0.
        # That is overlap, not a 0 ms response, so it is left out of latency,
        # unless the reply began in a pause in the caller's line.
        latency = turn.latency_ms
        if turn.role == "assistant" and latency is not None and latency <= 0:
            latency = pause_latency_ms(caller, turn)
        if turn.role == "assistant" and latency is not None and latency > 0:
            item["latency_ms"] = round(min(latency, 600000.0), 1)
        caller = turn if turn.role == "user" else None
        if getattr(turn, "interrupted", None):
            item["interrupted"] = True
        out.append(item)
    return out


def call_record(
    *,
    call_id: str,
    run: dict,
    golden: ConversationalGolden,
    case: ConversationalTestCase | None,
    metrics,
    error: str | None = None,
) -> dict:
    meta = golden.additional_metadata or {}
    record = {
        "id": call_id,
        "run": run,
        "demo": meta["demo"],
        "golden": golden.name,
        "scenario": golden.scenario[:400],
        "expected_outcome": (golden.expected_outcome or "")[:400],
        "status": "failed" if error else "ok",
        "turns": turns(case),
        "scores": scores(metrics) if case else [],
    }
    if error:
        record["error"] = error[:400]
    return record
