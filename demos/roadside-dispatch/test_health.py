from health import AudioHealth, band, ema


def test_band_thresholds():
    assert band(0.30) == "good"
    assert band(0.45) == "warn"
    assert band(0.75) == "bad"


def test_ema_seeds_then_smooths():
    assert ema(None, 0.8) == 0.8
    assert ema(0.0, 1.0, alpha=0.3) == 0.3


def test_update_smooths_risk():
    h = AudioHealth()
    h.update({"risk_score": 1.0, "noise": 1.0})
    assert h.risk == 1.0
    h.update({"risk_score": 0.0, "noise": 0.0})
    assert round(h.risk, 4) == 0.7  # 0.3*0 + 0.7*1.0


def test_driver_is_worst_degradation_excluding_loudness():
    h = AudioHealth()
    h.dims = {"noise": 0.7, "media_speech": 0.2, "speaker_loudness": 0.95}
    assert h.driver() == "noise"


def test_driver_none_when_all_below_warn():
    h = AudioHealth()
    h.dims = {"noise": 0.10, "media_speech": 0.05}
    assert h.driver() is None


def test_intervention_is_debounced_then_does_not_refire():
    h = AudioHealth()
    raw = {"risk_score": 0.9, "noise": 0.9}
    h.update(raw)
    assert h.newly_fired() == []        # one window: not yet
    h.update(raw)
    assert "noise" in h.newly_fired()   # two windows: fires
    assert h.newly_fired() == []        # already active: no refire


def test_intervention_clears_after_audio_recovers():
    h = AudioHealth()
    bad = {"risk_score": 0.9, "noise": 0.9}
    h.update(bad)
    h.update(bad)
    h.newly_fired()
    h.update({"risk_score": 0.0, "noise": 0.0})
    assert h.newly_cleared() == []      # smoothed noise still above threshold
    h.update({"risk_score": 0.0, "noise": 0.0})
    assert "noise" in h.newly_cleared() # second clean window drops it below


def test_field_gate_uses_band():
    h = AudioHealth()
    h.risk = 0.10
    assert h.field_state() == "clean"
    h.risk = 0.70
    assert h.field_state() == "needs_confirmation"
