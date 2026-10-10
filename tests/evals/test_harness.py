"""Offline tests for the eval harness: no LiveKit, no site, no paid API.

Run with: uv run pytest test_harness.py
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from caller import UiState, caller_graph, render, script, spell, spoken_date, stop_after_script
from deepeval.dataset import ConversationalGolden, EvaluationDataset
from deepeval.simulator.controller.types import Decision
from metrics import voice_metrics
from playground_api import EvalBusy, Site
from report import INFORMATIONAL, call_record, metric_key, scores
from speech import KOKORO_MODEL, MODELS, NoLLM

HERE = Path(__file__).parent


def goldens():
    dataset = EvaluationDataset()
    dataset.add_goldens_from_json_file(file_path=str(HERE / ".dataset.json"))
    return dataset.goldens


def packet(component: str, props: dict, topic: str = "ui", action: str = "update"):
    body = {"type": "ui_event", "component": component, "action": action, "props": props}
    return SimpleNamespace(topic=topic, data=json.dumps(body).encode())


def test_every_golden_names_a_demo_and_a_script():
    names = set()
    for golden in goldens():
        assert golden.name and golden.name not in names
        names.add(golden.name)
        assert golden.additional_metadata["demo"]
        assert script(golden)
        assert golden.persona is not None and golden.persona.speaks_first is False
        assert len(golden.scenario) <= 400 and len(golden.expected_outcome) <= 400


def test_lines_read_values_off_the_screen():
    ui = UiState()
    ui.on_data(packet("Refill", {"label": {"rx": "4471-B", "dob": "1984-03-12"}}))
    ui.on_data(packet("Other", {"x": 1}, topic="chat"))
    ui.on_data(SimpleNamespace(topic="ui", data=b"not json"))
    assert render("Rx {Refill.label.rx|spell}.", ui) == "Rx four four seven one, B."
    assert render("Born {Refill.label.dob|date}", ui) == "Born March 12, 1984"
    assert "Other" not in ui.components
    with pytest.raises(KeyError):
        render("{Refill.label.postal}", ui)
    ui.on_data(packet("Refill", {}, action="unmount"))
    assert ui.components == {}


def test_spell_and_dates():
    assert spell("M5V 2T6") == "M five V, two T six"
    assert spoken_date("2001-11-05") == "November 5, 2001"


def test_script_runs_in_order_and_stops_after_the_last_line():
    golden = ConversationalGolden(
        name="g",
        scenario="s",
        expected_outcome="e",
        additional_metadata={"demo": "coffee", "script": ["one", "two"]},
    )
    node = caller_graph(UiState())
    from deepeval.test_case import Turn

    assert node.edges == []  # no edges: the runner never calls the LLM to route
    assert node.action(turns=[], golden=golden) == "one"
    turns = [Turn(role="user", content="one"), Turn(role="assistant", content="ok")]
    assert node.action(turns=turns, golden=golden) == "two"
    assert stop_after_script(golden, 1) == Decision(should_end=False)
    assert stop_after_script(golden, 2).should_end
    with pytest.raises(ValueError):
        script(ConversationalGolden(scenario="s", additional_metadata={}))


def test_no_llm_refuses():
    with pytest.raises(RuntimeError):
        NoLLM().generate("hi")


def test_metric_keys_match_the_site():
    keys = [metric_key(m) for m in voice_metrics()]
    assert keys == [
        "voice_naturalness",
        "speech_intelligibility",
        "voice_consistency",
        "audio_integrity",
        "turn_taking_naturalness",
        "agent_responsiveness",
        "voice_reliability",
    ]


def test_failed_call_record():
    golden = goldens()[0]
    run = {"id": "run-1", "started_at": 1}
    record = call_record(call_id="abc", run=run, golden=golden, case=None, metrics=[], error="Boom")
    assert record["status"] == "failed" and record["error"] == "Boom"
    assert record["demo"] == "coffee" and record["turns"] == [] and record["scores"] == []


def site_with(handler) -> Site:
    return Site(
        base="https://site.test",
        secret="s" * 32,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_site_reserves_ends_and_records():
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, json.loads(request.content)))
        assert request.headers["authorization"] == "Bearer " + "s" * 32
        if request.url.path.endswith("eval-session") and request.method == "POST":
            return httpx.Response(
                200, json={"id": "c1", "url": "wss://x", "token": "t", "deadline": 9, "seconds": 90}
            )
        return httpx.Response(200, json={"ok": True})

    site = site_with(handler)
    call = site.start("coffee", 90)
    assert call.id == "c1" and call.token == "t"
    site.end("c1")
    site.record({"id": "c1"})
    assert seen == [
        ("POST", "/api/playground/eval-session", {"demo": "coffee", "seconds": 90}),
        ("DELETE", "/api/playground/eval-session", {"id": "c1"}),
        ("POST", "/api/playground/evals", {"action": "record", "id": "c1"}),
    ]


def test_site_gives_up_when_busy():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"error": "busy"})

    with pytest.raises(EvalBusy):
        site_with(handler).start("coffee", tries=2, wait=0)
    assert len(calls) == 2


def test_site_needs_a_secret(monkeypatch):
    monkeypatch.delenv("PLAYGROUND_EVAL_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        Site(base="https://site.test")


@pytest.mark.skipif(not (MODELS / KOKORO_MODEL).exists(), reason="Kokoro model not downloaded")
def test_scripted_call_end_to_end_against_a_fake_agent():
    """The real simulator, Kokoro and the voice metrics, with an in-process agent."""
    from deepeval.simulator import ConversationSimulator
    from deepeval.voice import CallbackVoiceConnector, ConnectorTurn, VoiceConfig
    from speech import KokoroTTS, WhisperSTT

    tts = KokoroTTS()
    replies = iter(["Sure, one latte. Anything else?", "Got it. Your total is five seventy-five."])

    async def agent(audio):
        text = next(replies)
        reply, _ = await tts.a_synthesize(text, voice="am_michael")
        return ConnectorTurn(audio=reply, transcript=text)

    golden = ConversationalGolden(
        name="fake",
        scenario="Orders a latte.",
        expected_outcome="One latte.",
        additional_metadata={"demo": "coffee", "script": ["A latte, please.", "That's all."]},
    )
    simulator = ConversationSimulator(
        voice_config=VoiceConfig(
            connector=lambda: CallbackVoiceConnector(agent),
            tts_model=tts,
            stt_model=WhisperSTT(),
            output_dir=None,
        ),
        simulation_graph=caller_graph(UiState()),
        stopping_controller=stop_after_script,
        simulator_model=NoLLM(),
        max_concurrent=1,
    )
    [case] = simulator.simulate([golden], max_user_simulations=3)
    assert [t.role for t in case.turns] == ["user", "assistant", "user", "assistant"]
    assert case.turns[0].content == "A latte, please."
    assert case.turns[1].content.startswith("Sure, one latte")

    metrics = voice_metrics()
    for metric in metrics:
        metric.measure(case, _show_indicator=False)
    record = call_record(
        call_id="c1",
        run={"id": "run-1", "started_at": 1},
        golden=golden,
        case=case,
        metrics=metrics,
    )
    assert record["status"] == "ok"
    assert [t["role"] for t in record["turns"]] == ["user", "assistant", "user", "assistant"]
    assert all(t.get("latency_ms", 0) >= 0 for t in record["turns"])
    assert len(record["scores"]) == 7
    computed = [s for s in record["scores"] if s["score"] is not None]
    assert computed, record["scores"]
    for score in computed:
        assert 0 <= score["score"] <= 1
        assert ("passed" in score) == (score["metric"] not in INFORMATIONAL)


def test_connector_drops_the_callers_transcript():
    from agent_connector import AgentConnector

    connector = AgentConnector(url="wss://example.invalid", token="t")
    connector._room = SimpleNamespace(
        local_participant=SimpleNamespace(track_publications={"TR_caller": object()})
    )
    seen = []

    class Task:
        def add_done_callback(self, _callback):
            pass

    def create_task(coro):
        seen.append(coro)
        coro.close()
        return Task()

    connector._loop = SimpleNamespace(create_task=create_task)

    def reader(track):
        return SimpleNamespace(info=SimpleNamespace(attributes={"lk.transcribed_track_id": track}))

    connector._on_transcript_stream(reader("TR_agent"), "agent")
    connector._on_transcript_stream(reader("TR_caller"), "agent")
    assert len(seen) == 1


def test_integrity_reason_names_the_defects():
    from deepeval.metrics import AudioIntegrityMetric

    metric = AudioIntegrityMetric()
    metric.score, metric.success = 0.4, False
    metric.reason = "Audio integrity was 0.40; 3 defect event(s) were detected."
    metric.score_breakdown = {
        "events": [
            {"type": "abrupt_cutoff"},
            {"type": "audio_dropout", "count": 2},
            {"type": "abrupt_cutoff"},
        ]
    }
    [score] = scores([metric])
    assert score["reason"].endswith("(abrupt_cutoff x2, audio_dropout x2)")


def test_audio_integrity_never_fails_a_call():
    from metrics import gating_metrics

    keys = {metric_key(m) for m in gating_metrics()}
    assert "audio_integrity" not in keys and "voice_reliability" not in keys
    metrics = voice_metrics()
    for metric in metrics:
        metric.score, metric.success, metric.reason = 0.0, False, "low"
    passed = {s["metric"]: s.get("passed") for s in scores(metrics)}
    assert passed["audio_integrity"] is None and passed["voice_naturalness"] is False
