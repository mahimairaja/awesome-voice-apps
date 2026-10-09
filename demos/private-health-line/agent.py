"""private-health-line: a clinical trial check-in line that keeps every byte in-house.

The research hospital's line calls a trial participant for their weekly symptom
diary. The agent asks six questions, records each answer through a validated
tool, and flags possible adverse events for the study coordinator.

What makes it different is where the call goes. Speech-to-text, the language
model and the voice all run on one self-hosted GPU box behind an
OpenAI-compatible API (speaches serves faster-whisper and Kokoro, vLLM serves
Qwen3). Every model request goes through a counting transport, so the agent can
show the bytes it sent, to which host, and how long each stage took. Bytes sent
to any host other than your own model server are counted separately; the point
of the demo is that the number stays at zero.

Run it:
1. Start the model server on a GPU machine: `docker compose up -d` (compose.yaml).
2. cp .env.example .env and fill it in.
3. uv sync
4. uv run python agent.py console
"""

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

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
)
from livekit.plugins import openai, silero

load_dotenv()

logger = logging.getLogger(__name__)

HOSPITAL = "Kestrel Bay Regional Health"
STAGES = ("stt", "llm", "tts")

FieldName = Literal["participant", "doses", "symptoms", "severity", "hospital", "medication"]
FIELDS: tuple[str, ...] = FieldName.__args__
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
_NUMBERS = {"none": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_NUMBERS |= {"six": 6, "seven": 7}


def _yes_no(value: str) -> bool | None:
    v = value.strip().lower().rstrip(".!")
    return True if v in _YES else False if v in _NO else None


def check_answer(field: str, value: str) -> tuple[bool, str, str | None]:
    """Validate one answer. Returns (ok, normalized value or reason, possible adverse event)."""
    v = value.strip()
    if not v:
        return False, "the answer came through empty; ask again", None
    if field == "participant":
        return True, v[:60], None
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
    return {"answers": {}, "flags": {}, "outcome": None, "ref": None}


@dataclass(frozen=True)
class PrivateHost:
    """Where the models run. Every value comes from the environment."""

    base_url: str
    api_key: str
    region: str
    gpu: str
    stt_model: str
    llm_model: str
    tts_model: str
    tts_voice: str

    @classmethod
    def from_env(cls) -> "PrivateHost":
        base_url = os.environ.get("ONPREM_BASE_URL", "").rstrip("/")
        parsed = urlparse(base_url)
        local = parsed.hostname in ("localhost", "127.0.0.1")
        if not parsed.hostname or (parsed.scheme != "https" and not local):
            raise RuntimeError("ONPREM_BASE_URL must be an https URL to your model server")
        if not os.environ.get("ONPREM_API_KEY"):
            raise RuntimeError("Missing ONPREM_API_KEY")
        return cls(
            base_url=base_url,
            api_key=os.environ["ONPREM_API_KEY"],
            region=os.environ.get("ONPREM_REGION", "your data centre"),
            gpu=os.environ.get("ONPREM_GPU", "one GPU"),
            stt_model=os.environ.get(
                "ONPREM_STT_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2"
            ),
            llm_model=os.environ.get("ONPREM_LLM_MODEL", "Qwen/Qwen3-4B-Instruct-2507"),
            tts_model=os.environ.get("ONPREM_TTS_MODEL", "speaches-ai/Kokoro-82M-v1.0-ONNX"),
            tts_voice=os.environ.get("ONPREM_TTS_VOICE", "af_heart"),
        )

    @property
    def host(self) -> str:
        return urlparse(self.base_url).hostname or ""


class EgressLedger:
    """Payload bytes each model stage sent and received, and anything sent elsewhere."""

    def __init__(self, private_host: str) -> None:
        self.private_host = private_host
        self.flows = {s: {"sent": 0, "received": 0, "requests": 0} for s in STAGES}
        self.elsewhere = 0
        self.on_change = None

    def add(self, stage: str, host: str, *, sent: int = 0, received: int = 0) -> None:
        flow = self.flows[stage]
        flow["sent"] += sent
        flow["received"] += received
        if sent:
            flow["requests"] += 1
        if host != self.private_host:
            self.elsewhere += sent + received
        if self.on_change:
            self.on_change()


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
    """Count every request and response body one model client moves, by host."""

    def __init__(self, ledger: EgressLedger, stage: str, inner=None) -> None:
        self._ledger = ledger
        self._stage = stage
        self._inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Model requests (a WAV clip, a chat history, a sentence) are small and buffered.
        body = await request.aread()
        host = request.url.host
        self._ledger.add(self._stage, host, sent=max(1, len(body)))
        response = await self._inner.handle_async_request(request)
        response.stream = _CountedStream(
            response.stream, lambda n: self._ledger.add(self._stage, host, received=n)
        )
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def private_stack(host: PrivateHost, ledger: EgressLedger) -> tuple[dict, list]:
    """STT, LLM and TTS clients that can only reach the private model server."""
    http_clients = []

    def client(stage: str) -> openai_sdk.AsyncClient:
        http = httpx.AsyncClient(
            transport=CountingTransport(ledger, stage),
            timeout=httpx.Timeout(30, connect=5),
        )
        http_clients.append(http)
        return openai_sdk.AsyncClient(
            base_url=host.base_url, api_key=host.api_key, max_retries=0, http_client=http
        )

    stack = {
        # Whisper is not a streaming model: VAD cuts each utterance and sends one WAV clip.
        "stt": openai.STT(
            model=host.stt_model, language="en", use_realtime=False, client=client("stt")
        ),
        "llm": openai.LLM(
            model=host.llm_model,
            client=client("llm"),
            max_completion_tokens=180,
            temperature=0.3,
            parallel_tool_calls=False,
        ),
        # Raw 24 kHz PCM: no decoder in the loop, and Kokoro speaks at 24 kHz.
        "tts": openai.TTS(
            model=host.tts_model,
            voice=host.tts_voice,
            response_format="pcm",
            client=client("tts"),
        ),
    }
    return stack, http_clients


class StageClock:
    """Per-turn server time: STT request, LLM first token, TTS first audio."""

    def __init__(self, keep: int = 6) -> None:
        self.turns: list[dict] = []
        self.keep = keep

    def _row(self, new: bool) -> dict:
        if new or not self.turns:
            self.turns.append({"stt": None, "llm": None, "tts": None})
            del self.turns[: -self.keep]
        return self.turns[-1]

    def stt(self, seconds: float) -> None:
        self._row(new=True)["stt"] = round(seconds * 1000)

    def llm(self, seconds: float) -> None:
        # A tool call makes a second LLM request in the same turn; the first one counts.
        row = self._row(new=False)
        if row["llm"] is None:
            row["llm"] = round(seconds * 1000)

    def tts(self, seconds: float) -> None:
        row = self._row(new=False)
        if row["tts"] is None:
            row["tts"] = round(seconds * 1000)


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


INSTRUCTIONS = (
    f"You are the clinical trial check-in line for {HOSPITAL}, a fictional Canadian "
    "research hospital. You are calling a participant in a drug trial for their "
    "weekly symptom diary. Ask these one at a time, in order: their participant "
    "number or name, how many of this week's seven daily doses they missed, any new "
    "or worse symptoms, how severe those are (none, mild, moderate or severe), "
    "whether they went to an emergency room or stayed in hospital since the last "
    "call, and whether they started any new medication. Call record_answer for each "
    "answer with the exact field name; if it is rejected, give the reason in one "
    "short sentence and ask again. If one answer covers several questions, record "
    "each. Never invent an answer. When all six are recorded, call complete_checkin "
    "and tell them the outcome and reference. Never give medical advice, never say "
    "whether a symptom is caused by the study drug, and never tell them to stop or "
    "change a dose. If they describe chest pain, trouble breathing or a severe "
    "allergic reaction, tell them to call 911 now. If asked where their voice goes: "
    "speech recognition, this model and this voice all run on the hospital's own "
    "servers, and no outside AI company receives the call. Keep replies to one or "
    "two short sentences, plain text, no lists."
)


class PrivateHealthLine(Agent):
    def __init__(self, room: rtc.Room, host: PrivateHost | None = None) -> None:
        self.host = host or PrivateHost.from_env()
        self.ledger = EgressLedger(self.host.host)
        self.clock = StageClock()
        self.stack, self._http_clients = private_stack(self.host, self.ledger)
        super().__init__(instructions=INSTRUCTIONS, **self.stack)
        self.room = room
        self._state: dict | None = None
        self._publish_pending = False
        self.ledger.on_change = self.schedule_publish
        self.stack["stt"].on("metrics_collected", lambda m: self._timed(self.clock.stt, m.duration))
        self.stack["llm"].on("metrics_collected", lambda m: self._timed(self.clock.llm, m.ttft))
        self.stack["tts"].on("metrics_collected", lambda m: self._timed(self.clock.tts, m.ttfb))

    def _timed(self, record, seconds: float) -> None:
        if seconds and seconds > 0:
            record(seconds)
            self.schedule_publish()

    def bind(self, state: dict) -> None:
        self._state = state

    def snapshot(self) -> dict:
        state = self._state or initial_state()
        return {
            "hospital": HOSPITAL,
            "host": {"region": self.host.region[:60], "gpu": self.host.gpu[:60]},
            "models": {
                "stt": self.host.stt_model.split("/")[-1][:60],
                "llm": self.host.llm_model.split("/")[-1][:60],
                "tts": self.host.tts_model.split("/")[-1][:60],
            },
            "form": [
                {
                    "field": f,
                    "label": LABELS[f],
                    "value": state["answers"].get(f, ""),
                    "flag": state["flags"].get(f) or "",
                }
                for f in FIELDS
            ],
            "outcome": state["outcome"],
            "ref": state["ref"],
            "flows": [{"stage": s, **self.ledger.flows[s]} for s in STAGES],
            "elsewhere": self.ledger.elsewhere,
            "turns": self.clock.turns,
        }

    def publish(self) -> None:
        self._publish_pending = False
        publish_ui_event(self.room, "PrivateLine", self.snapshot())

    def schedule_publish(self) -> None:
        # TTS audio arrives in many small chunks: send at most a few snapshots a second.
        if self._publish_pending:
            return
        try:
            asyncio.get_running_loop().call_later(0.25, self.publish)
        except RuntimeError:
            return
        self._publish_pending = True

    async def aclose_clients(self) -> None:
        for http in self._http_clients:
            await http.aclose()

    @function_tool()
    async def record_answer(self, context: RunContext[dict], field: FieldName, value: str) -> str:
        """Record one check-in answer.

        Fields: participant (number or name), doses (missed this week, 0-7),
        symptoms (new or worse, or none), severity (none, mild, moderate, severe),
        hospital (ER visit or stay, yes or no), medication (new medication, yes or no).
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
        missing = [LABELS[f] for f in FIELDS if f not in state["answers"]]
        if missing:
            return f"cannot complete yet, still missing: {', '.join(missing)}"
        state["ref"] = state["ref"] or _ref()
        flags = list(state["flags"].values())
        state["outcome"] = "coordinator" if flags else "routine"
        self.publish()
        if flags:
            return (
                f"Outcome: the study coordinator will call back today about "
                f"{', '.join(flags)}. "
                f"Reference {state['ref']}."
            )
        return f"Outcome: diary recorded, no callback needed. Reference {state['ref']}."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    # Voice activity detection runs inside this worker, on CPU.
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
            "and ask for their participant number or name."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
