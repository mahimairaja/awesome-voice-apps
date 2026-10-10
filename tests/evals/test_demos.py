"""Voice evals for the hosted playground demos.

Each golden in .dataset.json is one call: a scripted caller (Kokoro voice, no
LLM) talks to the live demo through LiveKit, DeepEval scores the call audio
with its local voice metrics, and the call is stored on mahimai.ca, where a
Claude routine later judges the transcript.

Run with: deepeval test run test_demos.py   (see README.md)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import deepeval
import pytest
from agent_connector import AgentConnector
from caller import UiState, caller_graph, script, stop_after_script
from deepeval import assert_test
from deepeval.dataset import EvaluationDataset
from deepeval.simulator import ConversationSimulator
from deepeval.voice import VoiceConfig
from metrics import voice_metrics
from playground_api import EvalBusy, Site
from report import call_record
from speech import KokoroTTS, NoLLM, WhisperSTT

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


def simulate(golden, call, speech):
    tts, stt, llm = speech
    ui = UiState()
    meta = golden.additional_metadata

    def connector():
        from livekit import rtc

        room = rtc.Room()
        ui.attach(room)
        return AgentConnector(
            url=call.url,
            token=call.token,
            room=room,
            turn_detection=meta.get("turn_detection", "balanced"),
        )

    simulator = ConversationSimulator(
        voice_config=VoiceConfig(
            connector=connector, tts_model=tts, stt_model=stt, output_dir=None
        ),
        simulation_graph=caller_graph(ui),
        stopping_controller=stop_after_script,
        simulator_model=llm,
        max_concurrent=1,
    )
    [case] = simulator.simulate([golden], max_user_simulations=len(script(golden)) + 1)
    return case


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
        case = simulate(golden, call, speech)
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

    assert_test(test_case=case, metrics=voice_metrics(), run_async=False)
