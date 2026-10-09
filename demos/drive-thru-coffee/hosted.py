"""Hosted coffee simulation: LiveKit worker with server-authorized call limits."""

import asyncio
import json
import logging
import os
import time
import uuid
from decimal import ROUND_CEILING, Decimal

import httpx
import voicegateway
from agent import DriveThruAttendant, _publish_cart, _publish_menu
from hosted_claim import ClaimIntake, publish_claim
from hosted_claim import initial_state as claim_state
from hosted_claim import instructions as claim_instructions
from hosted_clinic import ClinicScheduler, publish_clinic
from hosted_clinic import initial_state as clinic_state
from hosted_concierge import (
    CONCIERGE,
    HOTEL,
    HotelConcierge,
    SyncMeter,
    avatar_cost,
    publish_concierge,
)
from hosted_concierge import initial_state as concierge_state
from hosted_tenant import EMBED_USD_PER_TOKEN, TenantGuide, publish_tenant
from hosted_tenant import GREETING as TENANT_GREETING
from hosted_water import DEFAULT_GOAL, WaterCoach, publish_water
from hosted_water import initial_state as water_state
from livekit.agents import (
    AgentServer,
    AgentSession,
    APIConnectOptions,
    JobExecutorType,
    JobRequest,
    cli,
    get_job_context,
)
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.plugins import cartesia, deepgram, openai, silero
from trivia import QUESTIONS, HostedTriviaHost, publish_trivia
from trivia import initial_state as trivia_state
from voicegateway.services.inside_call import InsideCall
from voicegateway.services.sinks import RemoteCollectorSink

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
    if not origin.startswith(("https://", "http://127.0.0.1:")):
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
    if data.get("agent") not in DEMOS or room != f"playground-{reservation}":
        raise ValueError("Unapproved demo or room")
    return reservation


async def authorize(req: JobRequest) -> None:
    try:
        reservation = parse_job(req.job.metadata, req.room.name)
        approved = await control("claim", reservation)
        seconds = approved.get("seconds")
        if approved.get("id") != reservation or type(seconds) is not int or not 1 <= seconds <= 120:
            raise ValueError("Invalid reservation allowance")
        if approved.get("demo", "coffee") != json.loads(req.job.metadata)["agent"]:
            raise ValueError("Reservation demo mismatch")
        remaining = approved["deadline"] - time.time()
        if approved["room"] != req.room.name or not 0 < remaining <= 120:
            raise ValueError("Invalid reservation deadline")
        claims[req.room.name] = approved
    except Exception:  # noqa: BLE001 - every failed admission must reject the job
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

    def __init__(self, reservation: str, stop=None):
        super().__init__(os.environ["VOICEGW_COLLECTOR_URL"], os.environ["VOICEGW_API_KEY"])
        self.stop = stop
        self.reservation = reservation
        self.records = {}
        self.insights = InsideCall()
        self.report_lock = asyncio.Lock()

    async def log_request(self, record):
        if record.project == "mahimai-playground" and record.modality == "eou":
            await super().log_request(record)
            return
        if record.project != "mahimai-playground" or record.provider not in {
            "openai",
            "deepgram",
            "cartesia",
        }:
            raise ValueError("Unexpected playground cost attribution")
        self.insights.record(record)
        cost = Decimal(str(record.cost_usd))
        if not cost.is_finite() or cost < 0:
            raise ValueError("Invalid metered cost")
        self.records[record.id] = (
            record.provider,
            max(cost, self.records.get(record.id, (None, Decimal(0)))[1]),
        )
        if self.stop and (
            sum(value[1] for value in self.records.values()) >= Decimal("0.20")
            or getattr(record, "metadata", {}).get("pricing_complete") is False
        ):
            spawn(self.stop())
        await super().log_request(record)
        await self.report()

    async def report(self):
        async with self.report_lock:
            totals = {}
            for provider, cost in self.records.values():
                totals[provider] = totals.get(provider, Decimal(0)) + cost
            for provider, cost in totals.items():
                await control(
                    "usage",
                    self.reservation,
                    service=provider,
                    microusd=int((cost * 1_000_000).to_integral_value(rounding=ROUND_CEILING)),
                )

            await control("insights", self.reservation, summary=self.insights.snapshot())

    async def flush(self):
        await super().flush()
        await self.report()


class HostedGuard:
    """Call limits shared by every STT, LLM and TTS demo.

    A demo mixes this in front of its contributed agent and supplies the
    per-call state, the first UI events, and the opening instruction.
    """

    demo = "coffee"
    greeting = ""
    # LLM requests allowed in a full two-minute call; shorter calls scale down.
    llm_budget = 12

    def initial_state(self) -> dict:
        raise NotImplementedError

    def publish_initial(self) -> None:
        raise NotImplementedError

    def __init__(self) -> None:
        ctx = get_job_context()
        self.approval = claims.pop(ctx.room.name, None)
        if not self.approval:
            raise RuntimeError("Call has no approved reservation")
        super().__init__(ctx.room)
        # Per-agent clients keep component metrics isolated between concurrent calls.
        self.update_options(
            stt=deepgram.STT(model="nova-3"),
            llm=openai.LLM(
                model="gpt-4o-mini", max_completion_tokens=180, max_retries=0, store=False
            ),
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
        if (
            self._llm_requests > max(1, int(self.llm_budget * scale))
            or len(json.dumps(chat_ctx.to_dict()).encode()) > 16000
        ):
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
        self.session.userdata = self.initial_state()
        self._deadline_task = spawn(self._watch_deadline())
        self.session.on("close", lambda _: spawn(self._finish()))
        # Each visitor gets their own state. Never put a cart in pool session_kwargs.
        self.sink = PlaygroundSink(self.approval["id"])
        try:
            voicegateway.attach(
                self.session,
                project="mahimai-playground",
                agent_id=self.demo,
                sink=self.sink,
                room=self.room.name,
                transcript=False,
                snapshots=False,
                turns=False,
                dead_air=False,
            )
            self.publish_initial()
            await self.session.generate_reply(instructions=self.greeting)
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
        except (httpx.HTTPError, ValueError):
            logger.warning("Room cleanup pending server recovery")
        finally:
            await self.room.disconnect()


class HostedCoffee(HostedGuard, DriveThruAttendant):
    greeting = (
        "Say this is a coffee-ordering simulation, no real order or payment. "
        "Ask what they would like."
    )

    def initial_state(self) -> dict:
        return {"cart": []}

    def publish_initial(self) -> None:
        _publish_cart(self.room, self.session.userdata["cart"])
        _publish_menu(self.room)


class HostedTrivia(HostedGuard, HostedTriviaHost):
    demo = "trivia"
    greeting = "Welcome them to a three-question trivia game, then ask only: " + QUESTIONS[0][0]

    def initial_state(self) -> dict:
        return trivia_state()

    def publish_initial(self) -> None:
        publish_trivia(self.room, self.session.userdata)


class HostedWater(HostedGuard, WaterCoach):
    demo = "water"
    greeting = (
        "Say this is a hydration-tracking simulation and nothing is saved after the call. "
        f"Tell them the goal is {DEFAULT_GOAL} glasses today and ask how many they have had so far."
    )

    def initial_state(self) -> dict:
        return water_state()

    def publish_initial(self) -> None:
        publish_water(self.room, self.session.userdata)


class HostedTenant(HostedGuard, TenantGuide):
    demo = "tenant"
    greeting = TENANT_GREETING

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        publish_tenant(self.room, self._index)

    async def on_enter(self) -> None:
        await self.load_index()
        await super().on_enter()

    def meter_embedding(self, tokens: int) -> None:
        # VoiceGateway does not see direct embedding calls; bill them here.
        cost = Decimal(max(0, tokens)) * EMBED_USD_PER_TOKEN
        self.sink.records[f"tenant-embedding-{uuid.uuid4()}"] = ("openai", cost)
        spawn(self.sink.report())


class HostedClinic(HostedGuard, ClinicScheduler):
    demo = "clinic"
    greeting = (
        "Say this is a scheduling simulation for a demo clinic and no real appointment is made. "
        "Say the open slots are on screen and ask who the appointment is for and why."
    )

    def initial_state(self) -> dict:
        return clinic_state()

    def publish_initial(self) -> None:
        publish_clinic(self.room, self.session.userdata)


class HostedClaim(HostedGuard, ClaimIntake):
    demo = "claim"
    # Eight fields, a read-back and filing: each answer is a tool call plus a reply.
    llm_budget = 24
    greeting = (
        "Say this is an auto claim intake simulation: nothing is filed or saved, "
        "so use made-up details. Ask for their name to start the claim."
    )

    def __init__(self) -> None:
        super().__init__()
        self._instructions = claim_instructions()

    def initial_state(self) -> dict:
        return claim_state()

    def publish_initial(self) -> None:
        publish_claim(self.room, self.session.userdata)


class HostedConcierge(HostedGuard, HotelConcierge):
    """A concierge with a face: an Anam avatar speaks the agent's audio on video.

    The avatar is a third participant in the room. It is billed by the second
    from the moment it is requested, and the call ends with the reservation.
    """

    demo = "concierge"
    # Recommend, confirm, book, and maybe change the booking: each is a tool call plus a reply.
    llm_budget = 16
    greeting = (
        f"Say you are {CONCIERGE}, the concierge at {HOTEL}, a fictional hotel, and this is a "
        "simulation: no real table is booked. Ask what they feel like eating tonight."
    )

    def __init__(self) -> None:
        super().__init__()
        self._avatar_started = None
        self._billing_task = None
        self._meter = SyncMeter(self.room)

    def initial_state(self) -> dict:
        return concierge_state()

    def publish_initial(self) -> None:
        publish_concierge(self.room, self.session.userdata)
        self._meter.publish()

    async def start_avatar(self, session: AgentSession) -> None:
        """Bring the avatar into the room before the session says a word."""
        from livekit.plugins import anam

        self._meter.attach(session)
        avatar = anam.AvatarSession(
            persona_config=anam.PersonaConfig(
                name=CONCIERGE, avatarId=os.environ.get("ANAM_AVATAR_ID", "")
            ),
            avatar_participant_name=CONCIERGE,
            conn_options=APIConnectOptions(max_retry=0),
        )
        self._avatar_started = time.monotonic()
        try:
            await avatar.start(session, room=self.room)
            await avatar.wait_for_join(timeout=15)
        except Exception:
            # No session is running yet, so close the call here rather than in _finish.
            self._closing = True
            logger.warning("Avatar did not join; ending the concierge call")
            try:
                await control("usage", self.approval["id"], **self._avatar_usage())
                await control("finish", self.approval["id"])
            except (httpx.HTTPError, ValueError):
                logger.warning("Room cleanup pending server recovery")
            finally:
                await self.room.disconnect()
            raise

    def _avatar_usage(self) -> dict:
        elapsed = time.monotonic() - (self._avatar_started or time.monotonic())
        cost = avatar_cost(elapsed)
        return {
            "service": "anam",
            "microusd": int((cost * 1_000_000).to_integral_value(rounding=ROUND_CEILING)),
        }

    def bill_avatar(self) -> None:
        sink = getattr(self, "sink", None)
        if sink is not None and self._avatar_started is not None:
            sink.records["concierge-avatar"] = (
                "anam",
                avatar_cost(time.monotonic() - self._avatar_started),
            )

    async def on_enter(self) -> None:
        await super().on_enter()
        self._billing_task = spawn(self._bill_while_live())

    async def _bill_while_live(self) -> None:
        while not self._closing:
            self.bill_avatar()
            try:
                await self.sink.report()
            except (httpx.HTTPError, ValueError):
                logger.warning("Avatar usage report pending")
            await asyncio.sleep(5)

    async def _finish(self) -> None:
        if self._closing:
            return
        if self._billing_task and self._billing_task is not asyncio.current_task():
            self._billing_task.cancel()
        self.bill_avatar()
        if getattr(self, "sink", None) is not None:
            try:
                await self.sink.report()
            except (httpx.HTTPError, ValueError):
                logger.warning("Final avatar usage report pending")
        await super()._finish()


# The playground registry: each STT, LLM and TTS demo the site can reserve.
# GPT Live demos ("sdr") start their own session below. Adding a demo here also
# needs its id in the site's PLAYGROUND_DEMOS and a COPY line in the Dockerfile.
CASCADE_AGENTS: dict[str, type[HostedGuard]] = {
    "coffee": HostedCoffee,
    "trivia": HostedTrivia,
    "water": HostedWater,
    "tenant": HostedTenant,
    "clinic": HostedClinic,
    "claim": HostedClaim,
    "concierge": HostedConcierge,
}
DEMOS = frozenset({*CASCADE_AGENTS, "sdr"})


def build_server() -> AgentServer:
    for key in (
        "PLAYGROUND_WORKER_SECRET",
        "PLAYGROUND_ORIGIN",
        "VOICEGW_COLLECTOR_URL",
        "VOICEGW_API_KEY",
    ):
        if not os.environ.get(key):
            raise RuntimeError(f"Missing {key}")
    server = AgentServer(
        job_executor_type=JobExecutorType.THREAD,
        num_idle_processes=0,
        load_fnc=lambda worker: len(worker.active_jobs) / 2,
        load_threshold=1,
    )
    # Thread jobs share the immutable ONNX session; each VAD stream owns its state.
    # Loading another model for every call retains unnecessary inference memory.
    vad = silero.VAD.load()
    server.setup_fnc = lambda proc: proc.userdata.update(vad=vad)

    @server.rtc_session(agent_name="mahimai-playground-coffee", on_request=authorize)
    async def coffee(ctx):
        approval = claims.get(ctx.room.name, {})
        if approval.get("demo") == "sdr":
            from hosted_sdr import BACKEND_INSTRUCTIONS, HostedSDR

            claims.pop(ctx.room.name)
            session = AgentSession(
                llm=openai.realtime.GPTLiveModel(
                    voice="marin",
                    responses_options={
                        "model": "gpt-5.6-luna",
                        "instructions": BACKEND_INSTRUCTIONS,
                        "parallel_tool_calls": False,
                        "max_output_tokens": 600,
                        "reasoning": {"effort": "low"},
                    },
                ),
                vad=ctx.proc.userdata["vad"],
                conn_options=SessionConnectOptions(llm_conn_options=APIConnectOptions(max_retry=0)),
            )
            closing = False
            finished = asyncio.Event()
            sink = PlaygroundSink(approval["id"])

            async def finish_sdr():
                nonlocal closing
                if closing:
                    await finished.wait()
                    return
                closing = True
                session.shutdown(drain=False)
                try:
                    # GPT Live reports the final duration on close. Let metering
                    # finish before removing the room and ending the job loop.
                    try:
                        async with asyncio.timeout(8):
                            await session.aclose()
                            capture = getattr(session, "_vg_capture", None)
                            if capture:
                                await capture.reconcile(session)
                                await capture.drain()
                            await sink.flush()
                    except Exception:
                        logger.warning("SDR final metering pending; reservation retained")
                    await control("finish", approval["id"])
                except (httpx.HTTPError, ValueError):
                    logger.warning("SDR room cleanup pending server recovery")
                finally:
                    try:
                        await ctx.room.disconnect()
                    finally:
                        finished.set()

            sink.stop = finish_sdr

            async def deadline():
                await asyncio.sleep(max(0, approval["deadline"] - time.time()))
                await finish_sdr()

            timer = spawn(deadline())
            session.on("close", lambda _: spawn(finish_sdr()))
            agent = HostedSDR(ctx.room, approval, finish_sdr, spawn, sink)
            session.on(
                "user_input_transcribed",
                lambda event: agent.hear(event.transcript) if event.is_final else None,
            )

            async def cleanup():
                timer.cancel()
                await finish_sdr()

            ctx.add_shutdown_callback(cleanup)
            try:
                await ctx.connect()
                await session.start(agent=agent, room=ctx.room, record=False)
            except Exception:
                await cleanup()
                raise
            return
        session = AgentSession(
            conn_options=SessionConnectOptions(
                stt_conn_options=APIConnectOptions(max_retry=0),
                llm_conn_options=APIConnectOptions(max_retry=0),
                tts_conn_options=APIConnectOptions(max_retry=0),
            ),
            # Replaced per call in HostedGuard.on_enter; never share state between calls.
            userdata={},
            vad=ctx.proc.userdata["vad"],
            max_tool_steps=3,
            turn_handling={"turn_detection": "vad", "interruption": {"mode": "vad"}},
        )
        await ctx.connect()
        approval = claims.get(ctx.room.name, {})
        agent = CASCADE_AGENTS[approval.get("demo", "coffee")]()
        # Avatar demos route the session's audio to a video participant first.
        if hasattr(agent, "start_avatar"):
            await agent.start_avatar(session)
        await session.start(agent=agent, room=ctx.room, record=False)

    return server


if __name__ == "__main__":
    cli.run_app(build_server())
