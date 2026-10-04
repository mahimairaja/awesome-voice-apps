"""Hosted coffee simulation: LiveKit worker with server-authorized call limits."""

import asyncio
import json
import logging
import os
import time
import uuid
from decimal import Decimal, ROUND_CEILING

import httpx
import voicegateway
from livekit.agents import APIConnectOptions, AgentServer, AgentSession, JobExecutorType, JobRequest, cli, get_job_context
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.plugins import cartesia, deepgram, openai, silero
from voicegateway.services.sinks import RemoteCollectorSink

from agent import DriveThruAttendant, _publish_cart, _publish_menu

logger = logging.getLogger(__name__)
claims: dict[str, dict] = {}
tasks: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


async def control(action: str, reservation: str, **fields) -> dict:
    origin = os.environ["PLAYGROUND_ORIGIN"].rstrip("/")
    if not (origin.startswith("https://") or origin.startswith("http://127.0.0.1:")):
        raise ValueError("PLAYGROUND_ORIGIN must use HTTPS outside localhost")
    async with httpx.AsyncClient(timeout=5) as client:
        result = await client.post(
            f"{origin}/api/playground/worker",
            headers={"Authorization": f"Bearer {os.environ['PLAYGROUND_WORKER_SECRET']}"},
            json={"action": action, "id": reservation, **fields},
        )
        result.raise_for_status()
        return result.json()


def parse_job(metadata: str, room: str) -> str:
    data = json.loads(metadata)
    reservation = data.get("reservation", "")
    if str(uuid.UUID(reservation, version=4)) != reservation:
        raise ValueError("Invalid reservation")
    if data.get("agent") != "coffee" or room != f"playground-{reservation}":
        raise ValueError("Unapproved demo or room")
    return reservation


async def authorize(req: JobRequest) -> None:
    try:
        reservation = parse_job(req.job.metadata, req.room.name)
        approved = await control("claim", reservation)
        remaining = approved["deadline"] - time.time()
        if approved["room"] != req.room.name or not 0 < remaining <= 120:
            raise ValueError("Invalid reservation deadline")
        claims[req.room.name] = approved
    except Exception:
        # Do not log provider credentials, metadata or HTTP response bodies.
        logger.warning("Rejected unapproved playground call")
        await req.reject()
        return
    try:
        await req.accept()
    except Exception:
        claims.pop(req.room.name, None)
        raise


class PlaygroundSink(RemoteCollectorSink):
    """Attribute VoiceGateway costs to this reservation, never the shared account."""
    def __init__(self, reservation: str):
        super().__init__(os.environ["VOICEGW_COLLECTOR_URL"], os.environ["VOICEGW_API_KEY"])
        self.reservation = reservation
        self.records = {}
        self.report_lock = asyncio.Lock()

    async def log_request(self, record):
        if record.project == "mahimai-playground" and record.modality == "eou":
            await super().log_request(record)
            return
        if record.project != "mahimai-playground" or record.provider not in {"openai", "deepgram", "cartesia"}:
            raise ValueError("Unexpected playground cost attribution")
        cost = Decimal(str(record.cost_usd))
        if not cost.is_finite() or cost < 0:
            raise ValueError("Invalid metered cost")
        self.records[record.id] = (record.provider, max(cost, self.records.get(record.id, (None, Decimal(0)))[1]))
        await super().log_request(record)
        await self.report()

    async def report(self):
        async with self.report_lock:
            totals = {}
            for provider, cost in self.records.values():
                totals[provider] = totals.get(provider, Decimal(0)) + cost
            for provider, cost in totals.items():
                await control("usage", self.reservation, service=provider,
                              microusd=int((cost * 1_000_000).to_integral_value(rounding=ROUND_CEILING)))

    async def flush(self):
        await super().flush()
        await self.report()


class HostedCoffee(DriveThruAttendant):
    def __init__(self) -> None:
        ctx = get_job_context()
        self.approval = claims.pop(ctx.room.name, None)
        if not self.approval:
            raise RuntimeError("Call has no approved reservation")
        super().__init__(ctx.room)
        # Per-agent clients keep component metrics isolated between concurrent calls.
        self.update_options(
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(model="gpt-4o-mini", max_completion_tokens=180, max_retries=0, store=False),
            tts=cartesia.TTS(model="sonic-2"),
        )
        self._llm_requests = 0
        self._tts_bytes = 0
        self._closing = False
        self._deadline_task = None

    async def llm_node(self, chat_ctx, tools, model_settings):
        # Bound billable work before sending it, independently of delayed metrics.
        self._llm_requests += 1
        scale = self.approval["seconds"] / 120
        if self._llm_requests > max(1, int(12 * scale)) or len(json.dumps(chat_ctx.to_dict()).encode()) > 16000:
            spawn(self._finish())
            return
        async for chunk in super().llm_node(chat_ctx, tools, model_settings):
            yield chunk

    async def tts_node(self, text, model_settings):
        async def bounded_text():
            async for chunk in text:
                self._tts_bytes += len(chunk.encode())
                if self._tts_bytes > int(4000 * self.approval["seconds"] / 120):
                    spawn(self._finish())
                    return
                yield chunk
        async for frame in super().tts_node(bounded_text(), model_settings):
            yield frame

    async def on_enter(self) -> None:
        self._active_session = self.session
        self.session.userdata = {"cart": []}
        self._deadline_task = spawn(self._watch_deadline())
        self.session.on("close", lambda _: spawn(self._finish()))
        # Each visitor gets their own state. Never put a cart in pool session_kwargs.
        try:
            voicegateway.attach(
                self.session,
                project="mahimai-playground",
                agent_id="coffee",
                sink=PlaygroundSink(self.approval["id"]),
                room=self.room.name,
                transcript=False,
                snapshots=False,
                turns=False,
                dead_air=False,
            )
            _publish_cart(self.room, self.session.userdata["cart"])
            _publish_menu(self.room)
            await self.session.generate_reply(
                instructions="Say this is a coffee-ordering simulation, no real order or payment. Ask what they would like."
            )
        except Exception:
            await self._finish()
            raise

    async def _watch_deadline(self) -> None:
        await asyncio.sleep(max(0, self.approval["deadline"] - time.time()))
        await self._finish()

    async def _finish(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._deadline_task and self._deadline_task is not asyncio.current_task():
            self._deadline_task.cancel()
        self._active_session.shutdown(drain=False)
        try:
            await control("finish", self.approval["id"])
        except Exception:
            logger.warning("Room cleanup pending server recovery")
        finally:
            await self.room.disconnect()


def build_server() -> AgentServer:
    for key in ("PLAYGROUND_WORKER_SECRET", "PLAYGROUND_ORIGIN", "VOICEGW_COLLECTOR_URL", "VOICEGW_API_KEY"):
        if not os.environ.get(key):
            raise RuntimeError(f"Missing {key}")
    server = AgentServer(
        job_executor_type=JobExecutorType.THREAD,
        num_idle_processes=0,
        load_fnc=lambda worker: len(worker.active_jobs) / 2,
        load_threshold=1,
    )
    server.setup_fnc = lambda proc: proc.userdata.update(vad=silero.VAD.load())

    @server.rtc_session(agent_name="mahimai-playground-coffee", on_request=authorize)
    async def coffee(ctx):
        session = AgentSession(
            conn_options=SessionConnectOptions(
                stt_conn_options=APIConnectOptions(max_retry=0),
                llm_conn_options=APIConnectOptions(max_retry=0),
                tts_conn_options=APIConnectOptions(max_retry=0),
            ),
            userdata={"cart": []}, vad=ctx.proc.userdata["vad"], max_tool_steps=3,
            turn_handling={"turn_detection": "vad", "interruption": {"mode": "vad"}},
        )
        await ctx.connect()
        await session.start(agent=HostedCoffee(), room=ctx.room, record=False)

    return server


if __name__ == "__main__":
    cli.run_app(build_server())
