"""DeepEval's voice metrics that run locally on the call audio, with no LLM.

TranscriptionAccuracyMetric is left out on purpose: it needs an LLM judge, and
these evals run without a paid model. Goal, relevance, tool use and policy are
judged later from the stored transcript (see README.md).
"""

from deepeval.metrics import (
    AgentResponsivenessMetric,
    AudioIntegrityMetric,
    SpeechIntelligibilityMetric,
    TurnTakingNaturalnessMetric,
    VoiceConsistencyMetric,
    VoiceNaturalnessMetric,
    VoiceReliabilityMetric,
)
from report import INFORMATIONAL, metric_key


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


def gating_metrics():
    """The metrics a call must pass (see INFORMATIONAL in report.py)."""
    return [m for m in voice_metrics() if metric_key(m) not in INFORMATIONAL]


VOICE_METRICS = voice_metrics()
