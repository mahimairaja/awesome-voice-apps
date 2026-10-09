"""private-health-line: a clinical trial check-in where the model never hears who is calling.

A fictional Canadian research hospital calls a trial participant for their
weekly symptom diary. The agent asks six questions, records each answer through
a validated tool, and flags possible adverse events for the study coordinator.

The pattern is data minimisation. Three AI providers handle the call, and each
one gets only what its job needs:

- Deepgram (speech to text) hears the caller's voice. It has to.
- Our worker replaces any spoken number of three or more digits with
  "[participant number]" in the transcript, before anything else reads it, and
  keeps the real participant number itself.
- OpenAI (the language model) sees only that redacted transcript. Every request
  to it goes through a counting transport that also checks the request body for
  the participant number, so the screen can show how often it reached the model:
  zero.
- Cartesia (the voice) receives only the sentences the agent says.

Run it:
1. cp .env.example .env and fill it in.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Literal

import httpx
import openai as openai_sdk
from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    stt,
)
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger(__name__)

HOSPITAL = "Kestrel Bay Regional Health"
TOKEN = "[participant number]"
PROCESSORS = (
    {"stage": "stt", "provider": "Deepgram", "model": "nova-3", "host": "api.deepgram.com"},
    {"stage": "llm", "provider": "OpenAI", "model": "gpt-4o-mini", "host": "api.openai.com"},
    {"stage": "tts", "provider": "Cartesia", "model": "sonic-3", "host": "api.cartesia.ai"},
)

FieldName = Literal["doses", "symptoms", "severity", "hospital", "medication"]
FIELDS = ("participant", *FieldName.__args__)
LABELS = {
    "participant": "participant",
    "doses": "doses missed",
    "symptoms": "new symptoms",
    "severity": "severity",
    "hospital": "ER or hospital",
    "medication": "new medication",
}
SEVERITY = ("none", "mild", "moderate", "severe")
_YES = {"yes", "y", "yeah", "yep", "true", "i did", "i have", "i am"}
_NO = {"no", "n", "nope", "nah", "false", "i didn't", "i haven't", "not really", "none"}
_DIGITS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
_NUMBERS = {"none": 0, **{word: int(d) for word, d in _DIGITS.items() if word != "oh"}}
_WORD = re.compile(r"[A-Za-z]+|\d+")
_JOINER = re.compile(r"[\s,.-]*")


def redact(text: str) -> tuple[str, list[str]]:
    """Replace every spoken number of 3 to 8 digits with TOKEN.

    Speech to text writes a participant number as "412", "4 1 2" or "four one
    two"; all three become TOKEN. Single numbers ("two doses") are left alone.
    Returns the redacted text and the digits it removed.
    """
    runs: list[list[re.Match]] = [[]]
    for word in _WORD.finditer(text):
        if not (word.group().isdigit() or word.group().lower() in _DIGITS):
            runs.append([])
        elif runs[-1] and _JOINER.fullmatch(text[runs[-1][-1].end() : word.start()]):
            runs[-1].append(word)
        else:
            runs.append([word])
    found, out, last = [], [], 0
    for run in filter(None, runs):
        digits = "".join(
            w.group() if w.group().isdigit() else _DIGITS[w.group().lower()] for w in run
        )
        if 3 <= len(digits) <= 8:
            out += [text[last : run[0].start()], TOKEN]
            last = run[-1].end()
            found.append(digits)
    return "".join(out) + text[last:], found


def _yes_no(value: str) -> bool | None:
    v = value.strip().lower().rstrip(".!")
    return True if v in _YES else False if v in _NO else None


def check_answer(field: str, value: str) -> tuple[bool, str, str | None]:
    """Validate one answer. Returns (ok, normalized value or reason, possible adverse event)."""
    v = value.strip()
    if not v:
        return False, "the answer came through empty; ask again", None
    if field == "symptoms":
        return True, ("none" if _yes_no(v) is False else v[:60]), None
    if field == "doses":
        said = v.lower().rstrip(".")
        try:
            missed = _NUMBERS[said] if said in _NUMBERS else int(float(said))
        except ValueError:
            return False, "doses missed is a whole number from 0 to 7", None
        if not 0 <= missed <= 7:
            return False, "doses missed is a whole number from 0 to 7", None
        flag = f"missed {missed} doses" if missed >= 2 else None
        return True, f"{missed} of 7", flag
    if field == "severity":
        # "Mild, maybe moderate" is moderate: the worst word they used wins.
        said = v.lower()
        words = [w for w in SEVERITY if w in said]
        if not words:
            return False, f"describe it as one of: {', '.join(SEVERITY)}", None
        word = max(words, key=SEVERITY.index)
        return True, word, "severe symptoms" if word == "severe" else None
    if field in ("hospital", "medication"):
        answer = _yes_no(v)
        if answer is None:
            return False, "answer yes or no", None
        flag = "emergency or hospital visit" if field == "hospital" else "new medication"
        return True, "yes" if answer else "no", flag if answer else None
    return False, "unknown field", None


def initial_state() -> dict:
    return {"participant": None, "answers": {}, "flags": {}, "outcome": None, "ref": None}


class DataLedger:
    """What each provider received, measured where it leaves the worker."""

    def __init__(self) -> None:
        self.audio_ms = 0
        self.llm = {"sent": 0, "received": 0, "requests": 0}
        self.tts_chars = 0
        self.redacted = 0
        # Requests to the language model whose body contained the participant number.
        self.leaks = 0
        self.seen = ""
        self.secret: str | None = None
        self.on_change = None

    def changed(self) -> None:
        if self.on_change:
            self.on_change()

    def model_request(self, body: bytes) -> None:
        self.llm["sent"] += len(body)
        self.llm["requests"] += 1
        if self.secret and self.secret.encode() in body:
            self.leaks += 1
        try:
            messages = json.loads(body).get("messages", [])
            said = next(m["content"] for m in reversed(messages) if m.get("role") == "user")
            self.seen = (said if isinstance(said, str) else json.dumps(said))[:200]
        except (ValueError, AttributeError, KeyError, StopIteration, TypeError):
            pass
        self.changed()


class _CountedStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, count) -> None:
        self._inner = inner
        self._count = count

    async def __aiter__(self):
        async for chunk in self._inner:
            self._count(len(chunk))
            yield chunk

    async def aclose(self) -> None:
        await self._inner.aclose()


class CountingTransport(httpx.AsyncBaseTransport):
    """Measure and inspect every request the language model client sends."""

    def __init__(self, ledger: DataLedger, inner=None) -> None:
        self._ledger = ledger
        self._inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._ledger.model_request(await request.aread())
        response = await self._inner.handle_async_request(request)

        def received(n: int) -> None:
            self._ledger.llm["received"] += n
            self._ledger.changed()

        response.stream = _CountedStream(response.stream, received)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def counted_llm(ledger: DataLedger) -> tuple[openai.LLM, httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=CountingTransport(ledger), timeout=httpx.Timeout(30, connect=5)
    )
    client = openai_sdk.AsyncClient(max_retries=0, http_client=http)
    model = openai.LLM(
        model="gpt-4o-mini",
        client=client,
        max_completion_tokens=180,
        temperature=0.3,
        parallel_tool_calls=False,
        store=False,
    )
    return model, http


def publish_ui_event(room: rtc.Room, component: str, props: dict) -> None:
    payload = json.dumps(
        {"type": "ui_event", "component": component, "action": "update", "props": props}
    ).encode()
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except RuntimeError:
        logger.warning("No event loop for the ui event")
        return
    task.add_done_callback(
        lambda t: t.cancelled() or not t.exception() or logger.warning("ui event not delivered")
    )


def _ref() -> str:
    return f"KB-{uuid.uuid4().hex[:6].upper()}"


def _masked(digits: str | None) -> str:
    return "" if not digits else "•" * (len(digits) - 2) + digits[-2:]


INSTRUCTIONS = (
    f"You are the clinical trial check-in line for {HOSPITAL}, a fictional Canadian "
    "research hospital, calling a participant in a drug trial for their weekly "
    "symptom diary. First ask for their participant number. You never hear it: "
    f"our system replaces it with {TOKEN} and records it itself. When you see "
    f"{TOKEN}, thank them and move on; never ask them to repeat it unless "
    "complete_checkin says it is missing. Then ask these one at a time, in order: "
    "how many of this week's seven daily doses they missed, any new or worse "
    "symptoms, how severe those are (none, mild, moderate or severe), whether they "
    "went to an emergency room or stayed in hospital since the last call, and "
    "whether they started any new medication. Call record_answer for each answer "
    "with the exact field name; if it is rejected, give the reason in one short "
    "sentence and ask again. If one answer covers several questions, record each. "
    "Never invent an answer. When all are recorded, call complete_checkin and tell "
    "them the outcome and reference. Never give medical advice, never say whether a "
    "symptom is caused by the study drug, and never tell them to stop or change a "
    "dose. If they describe chest pain, trouble breathing or a severe allergic "
    "reaction, tell them to call 911 now. If asked where their voice goes: a speech "
    "recognition provider transcribes it, the participant number is removed before "
    "the language model reads anything, and the voice provider only receives what "
    "you say. Keep replies to one or two short sentences, plain text, no lists."
)


class PrivateHealthLine(Agent):
    def __init__(self, room: rtc.Room) -> None:
        self.ledger = DataLedger()
        llm, self._http = counted_llm(self.ledger)
        self.stack = {
            "stt": deepgram.STT(model="nova-3", language="en"),
            "llm": llm,
            "tts": cartesia.TTS(model="sonic-3"),
        }
        super().__init__(instructions=INSTRUCTIONS, **self.stack)
        self.room = room
        self._state: dict | None = None
        self._publish_pending = False
        self.ledger.on_change = self.schedule_publish

    def bind(self, state: dict) -> None:
        self._state = state

    def snapshot(self) -> dict:
        state = self._state or initial_state()
        amounts = {
            "stt": self.ledger.audio_ms,
            "llm": self.ledger.llm["sent"],
            "tts": self.ledger.tts_chars,
        }
        return {
            "hospital": HOSPITAL,
            "processors": [{**p, "amount": amounts[p["stage"]]} for p in PROCESSORS],
            "requests": self.ledger.llm["requests"],
            "received": self.ledger.llm["received"],
            "redacted": self.ledger.redacted,
            "leaks": self.ledger.leaks,
            "seen": self.ledger.seen,
            "form": [
                {
                    "field": f,
                    "label": LABELS[f],
                    "value": _masked(state["participant"])
                    if f == "participant"
                    else state["answers"].get(f, ""),
                    "flag": state["flags"].get(f) or "",
                }
                for f in FIELDS
            ],
            "outcome": state["outcome"],
            "ref": state["ref"],
        }

    def publish(self) -> None:
        self._publish_pending = False
        publish_ui_event(self.room, "PrivateLine", self.snapshot())

    def schedule_publish(self) -> None:
        # Audio and model bytes change many times a second: send a few snapshots a second.
        if self._publish_pending:
            return
        try:
            asyncio.get_running_loop().call_later(0.25, self.publish)
        except RuntimeError:
            return
        self._publish_pending = True

    async def aclose_clients(self) -> None:
        await self._http.aclose()

    def screen(self, event: stt.SpeechEvent) -> stt.SpeechEvent:
        """Redact the transcript before the session, the model or the history see it."""
        for alt in event.alternatives:
            alt.text, found = redact(alt.text)
            if found and event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                self.ledger.redacted += len(found)
                state = self._state
                if state is not None and not state["participant"]:
                    state["participant"] = found[0]
                    self.ledger.secret = found[0]
                self.schedule_publish()
        return event

    async def stt_node(self, audio, model_settings):
        async def counted():
            async for frame in audio:
                self.ledger.audio_ms += int(1000 * frame.samples_per_channel / frame.sample_rate)
                yield frame

        async for event in Agent.default.stt_node(self, counted(), model_settings):
            yield self.screen(event) if isinstance(event, stt.SpeechEvent) else event

    async def tts_node(self, text, model_settings):
        async def counted():
            async for chunk in text:
                self.ledger.tts_chars += len(chunk)
                self.schedule_publish()
                yield chunk

        async for frame in super().tts_node(counted(), model_settings):
            yield frame

    @function_tool()
    async def record_answer(self, context: RunContext[dict], field: FieldName, value: str) -> str:
        """Record one diary answer.

        Fields: doses (missed this week, 0-7), symptoms (new or worse, or none),
        severity (none, mild, moderate, severe), hospital (ER visit or stay, yes or
        no), medication (new medication, yes or no).
        """
        ok, result, flag = check_answer(field, value)
        if not ok:
            return f"rejected: {result}."
        state = context.userdata
        state["answers"][field] = result
        state["flags"].pop(field, None)
        if flag:
            state["flags"][field] = flag
        self.publish()
        if flag:
            return (
                f"recorded {field}: {result}. This is a possible adverse event for the "
                "study coordinator; do not reassure or explain it."
            )
        return f"recorded {field}: {result}"

    @function_tool()
    async def complete_checkin(self, context: RunContext[dict]) -> str:
        """Finish the check-in. Refuses until every answer is recorded."""
        state = context.userdata
        missing = [LABELS[f] for f in FieldName.__args__ if f not in state["answers"]]
        if not state["participant"]:
            missing.insert(0, "participant number (ask them to say it again)")
        if missing:
            return f"cannot complete yet, still missing: {', '.join(missing)}"
        state["ref"] = state["ref"] or _ref()
        flags = list(state["flags"].values())
        state["outcome"] = "coordinator" if flags else "routine"
        self.publish()
        if flags:
            return (
                f"Outcome: the study coordinator will call back today about "
                f"{', '.join(flags)}. Reference {state['ref']}."
            )
        return f"Outcome: diary recorded, no callback needed. Reference {state['ref']}."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="private-health-line")
async def entrypoint(ctx: JobContext) -> None:
    state = initial_state()
    agent = PrivateHealthLine(ctx.room)
    agent.bind(state)
    session = AgentSession(
        userdata=state,
        vad=ctx.proc.userdata["vad"],
        turn_handling={"turn_detection": "vad"},
        max_tool_steps=3,
    )
    ctx.add_shutdown_callback(agent.aclose_clients)
    await ctx.connect()
    await session.start(agent=agent, room=ctx.room)
    agent.publish()
    await session.generate_reply(
        instructions=(
            f"Say you are the {HOSPITAL} clinical trial check-in line, a simulation, "
            "and ask for their participant number."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
