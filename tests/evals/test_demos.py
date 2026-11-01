"""Voice evals for the hosted playground demos.

Each golden in .dataset.json is one call: a scripted caller (Kokoro voice, no
LLM) talks to the live demo through LiveKit, DeepEval scores the call audio
with its local voice metrics, and the call is stored on mahimai.ca, where a
Claude routine later judges the transcript.

Run with: deepeval test run test_demos.py   (see README.md)
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import deepeval
import pytest
from agent_connector import AgentConnector
from caller import UiState, caller_graph, patient_simulator, script, stop_after_script
from deepeval import assert_test
from deepeval.dataset import EvaluationDataset
from deepeval.voice import VoiceConfig
from metrics import gating_metrics, voice_metrics, word_metrics
from playground_api import EvalBusy, Site
from report import call_record
from speech import KokoroTTS, NoLLM, WhisperSTT, wav_samples

log = logging.getLogger("evals")

HERE = Path(__file__).parent
dataset = EvaluationDataset()
dataset.add_goldens_from_json_file(file_path=str(HERE / ".dataset.json"))

# EVAL_DEMOS=coffee,pharmacy runs only those demos; empty runs every golden.
only = {d for d in os.environ.get("EVAL_DEMOS", "").split(",") if d}
goldens = [g for g in dataset.goldens if not only or g.additional_metadata["demo"] in only]

RUN = {
    "id": os.environ.get("EVAL_RUN_ID") or f"local-{int(time.time())}",
    "started_at": int(time.time()),
    "commit_sha": os.environ.get("GITHUB_SHA"),
    "tool_version": f"deepeval-{deepeval.__version__}",
}


@pytest.fixture(scope="session")
def site() -> Site:
    return Site()


@pytest.fixture(scope="session")
def speech():
    return KokoroTTS(), WhisperSTT(), NoLLM()


# EVAL_AUDIO_DIR=recordings keeps each call's audio for a listen.
AUDIO_DIR = os.environ.get("EVAL_AUDIO_DIR") or None


def simulate(golden, call, speech):
    """Place one call. Returns the test case and what the agent heard the caller say."""
    tts, stt, llm = speech
    ui = UiState()
    meta = golden.additional_metadata
    connectors = []

    def connector():
        from livekit import rtc

        room = rtc.Room()
        ui.attach(room)
        made = AgentConnector(
            url=call.url,
            token=call.token,
            room=room,
            turn_detection=meta.get("turn_detection", "balanced"),
        )
        connectors.append(made)
        return made

    simulator = patient_simulator()(
        voice_config=VoiceConfig(
            connector=connector, tts_model=tts, stt_model=stt, output_dir=AUDIO_DIR
        ),
        simulation_graph=caller_graph(ui),
        stopping_controller=stop_after_script,
        simulator_model=llm,
        max_concurrent=1,
    )
    [case] = simulator.simulate([golden], max_user_simulations=len(script(golden)) + 1)
    heard = connectors[-1].heard if connectors else []
    if connectors:
        describe(golden.name, case, connectors[-1])
    return case, heard


def describe(name, case, connector) -> None:
    """Log each turn's timing and loudness, to tell harness faults from agent faults."""
    log.warning(
        "%s: agent transcript streams=%d, heard=%r, waits=%s",
        name,
        connector.agent_streams,
        connector.heard,
        [round(w, 2) for w in connector.waits],
    )
    for i, turn in enumerate(case.turns):
        line = f"{name} #{i} {turn.role}: {turn.content!r}"
        audio = getattr(turn, "audio", None)
        if audio is not None:
            samples, rate = wav_samples(audio)
            rms = float((samples**2).mean() ** 0.5) if len(samples) else 0.0
            peak = float(abs(samples).max()) if len(samples) else 0.0
            line += (
                f" start={getattr(audio, 'start_time', None)} secs={len(samples) / rate:.2f}"
                f" rate={rate} rms={rms:.4f} peak={peak:.3f}"
            )
        if turn.metadata:
            line += f" meta={turn.metadata}"
        log.warning(line)


@pytest.mark.parametrize("golden", goldens, ids=[g.name for g in goldens])
def test_demo(golden, site, speech):
    meta = golden.additional_metadata
    try:
        call = site.start(meta["demo"], meta.get("seconds"))
    except EvalBusy as busy:
        pytest.skip(str(busy))

    case, error = None, None
    measured = voice_metrics()
    try:
        case, heard = simulate(golden, call, speech)
        said = [t.content for t in case.turns if t.role == "user" and t.content]
        measured += word_metrics(said, heard, speech[1])
        for metric in measured:
            metric.measure(case, _show_indicator=False)
    except Exception as exc:  # the call failed; store that, then fail the test
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            site.end(call.id)
        finally:
            site.record(
                call_record(
                    call_id=call.id,
                    run=RUN,
                    golden=golden,
                    case=case,
                    metrics=measured,
                    error=error,
                )
            )

    assert_test(test_case=case, metrics=gating_metrics(), run_async=False)
