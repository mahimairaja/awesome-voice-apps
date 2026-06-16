"""Pure audio-health logic for the roadside-dispatch demo.

No LiveKit, Tyto, or asyncio here: just smoothing, banding, the driver, the
field-accuracy gate, and the intervention state machine, so the application
logic the demo quality bar cares about can be unit-tested in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Tyto's documented bands and recommended smoothing.
BAND_WARN = 0.35
BAND_BAD = 0.60
EMA_ALPHA = 0.3
CONSECUTIVE_WINDOWS = 2  # armed windows before an intervention episode fires

# The six Tyto dimensions by SDK field name. Loudness is a neutral level meter.
DIMENSIONS = (
    "noise",
    "speaker_reverb",
    "speaker_loudness",
    "interfering_speech",
    "media_speech",
    "packet_loss",
)
NEUTRAL = "speaker_loudness"
DEGRADATIONS = tuple(d for d in DIMENSIONS if d != NEUTRAL)

# Per-degradation thresholds that arm an intervention. Tuned in the dry run.
INTERVENTION_THRESHOLDS = {
    "noise": 0.60,
    "speaker_reverb": 0.60,
    "interfering_speech": 0.60,
    "media_speech": 0.50,
    "packet_loss": 0.50,
}


def band(score: float) -> str:
    if score < BAND_WARN:
        return "good"
    if score < BAND_BAD:
        return "warn"
    return "bad"


def ema(prev: float | None, value: float, alpha: float = EMA_ALPHA) -> float:
    if prev is None:
        return value
    return alpha * value + (1 - alpha) * prev


@dataclass
class AudioHealth:
    """Smoothed Tyto scores plus the intervention and gate state machine."""

    risk: float | None = None
    dims: dict[str, float] = field(default_factory=dict)
    _streak: dict[str, int] = field(default_factory=dict)
    active: set[str] = field(default_factory=set)

    def update(self, raw: dict[str, float]) -> None:
        """Fold one raw Tyto window (risk_score + dimensions) into the state."""
        self.risk = ema(self.risk, raw["risk_score"])
        for d in DIMENSIONS:
            self.dims[d] = ema(self.dims.get(d), raw.get(d, 0.0))
        for d in DEGRADATIONS:
            armed = self.dims[d] >= INTERVENTION_THRESHOLDS[d]
            self._streak[d] = self._streak.get(d, 0) + 1 if armed else 0

    @property
    def band(self) -> str:
        return band(self.risk if self.risk is not None else 0.0)

    def driver(self) -> str | None:
        """The degradation contributing most to current risk, or None when clean."""
        if not self.dims:
            return None
        worst = max(DEGRADATIONS, key=lambda d: self.dims.get(d, 0.0))
        return worst if self.dims.get(worst, 0.0) >= BAND_WARN else None

    def newly_fired(self) -> list[str]:
        """Degradations that just crossed into a firing episode (debounced)."""
        fired = []
        for d in DEGRADATIONS:
            if self._streak.get(d, 0) >= CONSECUTIVE_WINDOWS and d not in self.active:
                self.active.add(d)
                fired.append(d)
        return fired

    def newly_cleared(self) -> list[str]:
        """Active degradations whose smoothed value fell back below threshold."""
        cleared = []
        for d in list(self.active):
            if self._streak.get(d, 0) == 0:
                self.active.discard(d)
                cleared.append(d)
        return cleared

    def field_state(self) -> str:
        """The gate's verdict for a field captured right now."""
        return "clean" if self.band == "good" else "needs_confirmation"
