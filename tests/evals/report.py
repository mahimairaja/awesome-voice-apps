"""Turn a simulated call and its metric results into the site's call record."""

from __future__ import annotations

import re

from deepeval.dataset import ConversationalGolden
from deepeval.test_case import ConversationalTestCase


def metric_key(metric) -> str:
    """'VoiceNaturalnessMetric' -> 'voice_naturalness'."""
    name = type(metric).__name__.removesuffix("Metric")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def scores(metrics) -> list[dict]:
    out = []
    for metric in metrics:
        score = getattr(metric, "score", None)
        reason = getattr(metric, "reason", None) or getattr(metric, "error", None)
        out.append(
            {
                "metric": metric_key(metric),
                "score": None if score is None else round(min(max(float(score), 0.0), 1.0), 4),
                "passed": None if score is None else bool(getattr(metric, "success", False)),
                "reason": str(reason)[:400] if reason else None,
            }
        )
    # The site rejects a null passed flag; drop the key instead.
    return [{k: v for k, v in s.items() if v is not None or k == "score"} for s in out]


def turns(case: ConversationalTestCase | None) -> list[dict]:
    out = []
    for turn in (case.turns if case else [])[:80]:
        item = {"role": turn.role, "text": (turn.content or "")[:2000]}
        if turn.audio is not None and turn.audio.start_time is not None:
            item["start_s"] = round(turn.audio.start_time, 3)
        if turn.role == "assistant" and turn.latency_ms is not None:
            item["latency_ms"] = round(min(max(turn.latency_ms, 0.0), 600000.0), 1)
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
