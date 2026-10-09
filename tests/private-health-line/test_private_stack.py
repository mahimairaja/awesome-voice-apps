"""Offline: the three model clients reach only the configured server, and every
byte and stage time is counted. A local fake stands in for speaches and vLLM."""

import asyncio
import json

from aiohttp import web
from livekit import rtc
from livekit.agents import llm

import agent

PORT = 8765


async def _transcribe(request):
    assert request.headers["Authorization"] == "Bearer offline"
    await request.post()
    return web.json_response({"text": "I missed two doses"})


async def _chat(request):
    body = await request.json()
    assert body["model"] == "Qwen/Qwen3-4B-Instruct-2507"
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    for text in ("Thanks,", " noted."):
        chunk = {
            "id": "1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": body["model"],
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
        }
        await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
    await response.write(b"data: [DONE]\n\n")
    return response


async def _speech(request):
    body = await request.json()
    assert (body["model"], body["response_format"]) == (
        "speaches-ai/Kokoro-82M-v1.0-ONNX",
        "pcm",
    )
    response = web.StreamResponse(headers={"Content-Type": "audio/pcm"})
    await response.prepare(request)
    await response.write(b"\0" * 48000)  # one second of 24 kHz 16-bit mono
    return response


async def _run(monkeypatch):
    monkeypatch.setenv("ONPREM_BASE_URL", f"http://127.0.0.1:{PORT}/v1")
    monkeypatch.setenv("ONPREM_API_KEY", "offline")
    app = web.Application()
    app.router.add_post("/v1/audio/transcriptions", _transcribe)
    app.router.add_post("/v1/chat/completions", _chat)
    app.router.add_post("/v1/audio/speech", _speech)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", PORT).start()
    host = agent.PrivateHost.from_env()
    ledger = agent.EgressLedger(host.host)
    stack, clients = agent.private_stack(host, ledger)
    try:
        clip = rtc.AudioFrame(b"\0\0" * 16000, 16000, 1, 16000)
        heard = await stack["stt"].recognize([clip])
        assert heard.alternatives[0].text == "I missed two doses"

        chat = llm.ChatContext()
        chat.add_message(role="user", content="hi")
        reply = ""
        async with stack["llm"].chat(chat_ctx=chat) as stream:
            async for chunk in stream:
                reply += chunk.delta.content if chunk.delta and chunk.delta.content else ""
        assert reply == "Thanks, noted."

        samples = 0
        async with stack["tts"].synthesize("Thanks, noted.") as stream:
            async for audio in stream:
                samples += audio.frame.samples_per_channel
        assert samples >= 24000
    finally:
        for client in clients:
            await client.aclose()
        await runner.cleanup()
    return ledger


def test_every_stage_goes_to_the_private_server_and_is_counted(monkeypatch):
    ledger = asyncio.run(_run(monkeypatch))
    assert ledger.elsewhere == 0
    for stage in agent.STAGES:
        assert ledger.flows[stage]["requests"] == 1
        assert ledger.flows[stage]["sent"] > 0 and ledger.flows[stage]["received"] > 0
    # The WAV clip goes up; one second of PCM comes down.
    assert ledger.flows["stt"]["sent"] > 32000
    assert ledger.flows["tts"]["received"] == 48000


def test_doses_severity_and_visits_raise_the_right_flags():
    assert agent.check_answer("doses", "two") == (True, "2 of 7", "missed 2 doses")
    assert agent.check_answer("doses", "0")[1:] == ("0 of 7", None)
    assert not agent.check_answer("doses", "nine")[0]
    assert agent.check_answer("severity", "mild, maybe severe")[2] == "severe symptoms"
    assert agent.check_answer("severity", "moderate")[1:] == ("moderate", None)
    assert agent.check_answer("symptoms", "no")[1] == "none"
    assert agent.check_answer("hospital", "yes")[2] == "emergency or hospital visit"
    assert agent.check_answer("medication", "no") == (True, "no", None)
