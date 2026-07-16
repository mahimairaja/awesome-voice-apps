from agent import PyannoteLive


def test_first_start_sets_current_and_labels():
    s = PyannoteLive(api_key="x")
    s.on_event({"type": "diarization_speaker_start", "data": {"speaker": "spk_a"}}, now=0.0)
    assert s.current_speaker == "spk_a"
    assert s.label("spk_a") == "Speaker 1"
    assert s.active == {"spk_a"}


def test_second_speaker_gets_next_label():
    s = PyannoteLive(api_key="x")
    s.on_event({"type": "diarization_speaker_start", "data": {"speaker": "spk_a"}}, now=0.0)
    s.on_event({"type": "diarization_speaker_start", "data": {"speaker": "spk_b"}}, now=1.0)
    assert s.label("spk_b") == "Speaker 2"
    assert s.current_speaker == "spk_b"


def test_end_accumulates_seconds_and_falls_back():
    s = PyannoteLive(api_key="x")
    s.on_event({"type": "diarization_speaker_start", "data": {"speaker": "spk_a"}}, now=0.0)
    s.on_event({"type": "diarization_speaker_start", "data": {"speaker": "spk_b"}}, now=2.0)
    s.on_event({"type": "diarization_speaker_end", "data": {"speaker": "spk_b"}}, now=5.0)
    assert s.seconds["spk_b"] == 3.0
    assert s.current_speaker == "spk_a"  # falls back to the still-active speaker


def test_top_level_speaker_field_is_accepted():
    s = PyannoteLive(api_key="x")
    s.on_event({"type": "diarization_speaker_start", "speaker": "spk_a"}, now=0.0)
    assert s.current_speaker == "spk_a"


def test_error_message_is_ignored_safely():
    s = PyannoteLive(api_key="x")
    s.on_event({"type": "error", "message": "bad"}, now=0.0)
    assert s.current_speaker is None
    assert s.active == set()


def test_talk_time_meters_normalize_and_flag_driver():
    s = PyannoteLive(api_key="x")
    s.on_event({"type": "diarization_speaker_start", "data": {"speaker": "spk_a"}}, now=0.0)
    s.on_event({"type": "diarization_speaker_end", "data": {"speaker": "spk_a"}}, now=1.0)
    s.on_event({"type": "diarization_speaker_start", "data": {"speaker": "spk_b"}}, now=1.0)
    s.on_event({"type": "diarization_speaker_end", "data": {"speaker": "spk_b"}}, now=4.0)
    items = s.talk_time_items(now=4.0)
    by_label = {it["label"]: it for it in items}
    assert by_label["Speaker 2"]["driver"] is True
    assert by_label["Speaker 1"]["driver"] is False
    assert 0.0 <= by_label["Speaker 1"]["value"] <= 1.0
