"""Offline: the participant number is cut from the transcript before the model
client sees it, and the counting transport would catch it if it ever got through."""

import asyncio
import json

import httpx
from livekit.agents import stt

import agent


def test_spoken_participant_numbers_are_redacted():
    assert agent.redact("my number is 4 1 2 7") == ("my number is [participant number]", ["4127"])
    assert agent.redact("four one two seven.") == ("[participant number].", ["4127"])
    assert agent.redact("I missed two, maybe three") == ("I missed two, maybe three", [])
    assert agent.redact("one of seven")[1] == []


def test_model_requests_are_counted_and_checked_for_the_number():
    ledger = agent.DataLedger()
    ledger.secret = "4127"
    inner = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=httpx.ByteStream(b"y" * 120))
    )

    async def run():
        async with httpx.AsyncClient(transport=agent.CountingTransport(ledger, inner)) as client:
            for said in ("It's [participant number].", "it is 4127"):
                body = {"messages": [{"role": "user", "content": said}]}
                await client.post("https://api.openai.com/v1/chat/completions", json=body)

    asyncio.run(run())
    assert ledger.llm["requests"] == 2 and ledger.llm["received"] == 240
    assert ledger.llm["sent"] > len(json.dumps({"messages": []}))
    assert ledger.leaks == 1
    assert ledger.seen == "it is 4127"


def test_final_transcripts_record_the_number_once(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "offline")
    monkeypatch.setenv("CARTESIA_API_KEY", "offline")
    line = agent.PrivateHealthLine(room=None)
    state = agent.initial_state()
    line.bind(state)
    for text in ("four one two seven", "actually 5550"):
        line.screen(
            stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[stt.SpeechData(language="en", text=text)],
            )
        )
    assert state["participant"] == "4127" and line.ledger.redacted == 2
    asyncio.run(line.aclose_clients())


def test_a_number_split_across_final_transcripts_is_redacted(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "offline")
    monkeypatch.setenv("CARTESIA_API_KEY", "offline")
    line = agent.PrivateHealthLine(room=None)
    state = agent.initial_state()
    line.bind(state)

    def say(text, kind=stt.SpeechEventType.FINAL_TRANSCRIPT):
        event = stt.SpeechEvent(type=kind, alternatives=[stt.SpeechData(language="en", text=text)])
        return line.screen(event).alternatives[0].text

    assert say("it's four one") == "it's [participant number]"
    assert say("two seven", stt.SpeechEventType.INTERIM_TRANSCRIPT) == "[participant number]"
    assert say("two seven.") == "[participant number]."
    assert state["participant"] == "4127" and line.ledger.redacted == 1
    # Once the number is recorded, short answers pass through untouched.
    assert say("I missed two") == "I missed two"
    asyncio.run(line.aclose_clients())


def test_doses_severity_and_visits_raise_the_right_flags():
    assert agent.check_answer("doses", "two") == (True, "2 of 7", "missed 2 doses")
    assert agent.check_answer("doses", "0")[1:] == ("0 of 7", None)
    assert not agent.check_answer("doses", "nine")[0]
    assert agent.check_answer("severity", "mild, maybe severe")[2] == "severe symptoms"
    assert agent.check_answer("severity", "moderate")[1:] == ("moderate", None)
    assert agent.check_answer("symptoms", "no")[1] == "none"
    assert agent.check_answer("hospital", "yes")[2] == "emergency or hospital visit"
    assert agent.check_answer("medication", "no") == (True, "no", None)
