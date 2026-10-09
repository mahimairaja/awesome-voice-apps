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
from hosted_checkout import GREETING as CHECKOUT_GREETING
from hosted_checkout import VoiceCheckout
from hosted_claim import ClaimIntake, publish_claim
from hosted_claim import initial_state as claim_state
from hosted_city311 import GREETING as CITY311_GREETING
from hosted_city311 import City311Agent, make_stt, make_tts, publish_city311
from hosted_city311 import initial_state as city311_state
from hosted_claim import instructions as claim_instructions
from hosted_clinic import ClinicScheduler, publish_clinic
from hosted_clinic import initial_state as clinic_state
from hosted_panel import GREETING as PANEL_GREETING
from hosted_panel import SoloPanelScribe, publish_panel
from hosted_pharmacy import GREETING as PHARMACY_GREETING
from hosted_pharmacy import RefillLine, make_stt, publish_refill
from hosted_pharmacy import initial_state as pharmacy_state
from hosted_pharmacy import instructions as pharmacy_instructions
from hosted_furnace import COMPANY as FURNACE_COMPANY
from hosted_furnace import DETECTOR_OPTIONS as FURNACE_DETECTOR
from hosted_furnace import FurnaceLine, publish_furnace
from hosted_furnace import initial_state as furnace_state
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
from livekit.agents.voice.room_io import RoomOptions
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


class HostedPanel(HostedGuard, SoloPanelScribe):
    demo = "panel"
    greeting = PANEL_GREETING

    def __init__(self) -> None:
        super().__init__()
        # One recap writes a row per seat in a single tool call; 180 tokens truncates it.
        self.update_options(
            llm=openai.LLM(
                model="gpt-4o-mini", max_completion_tokens=450, max_retries=0, store=False
            )
        )

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        publish_panel(self.room)


class HostedPharmacy(HostedGuard, RefillLine):
    demo = "pharmacy"
    # Five fields, each captured, read back and confirmed, then the refill.
    llm_budget = 30
    greeting = PHARMACY_GREETING

    def __init__(self) -> None:
        super().__init__()
        self._instructions = pharmacy_instructions()
        # Nova-3 keyterms for the formulary; smart format writes dates as dates.
        self.update_options(stt=make_stt())

    def initial_state(self) -> dict:
        return pharmacy_state()

    def publish_initial(self) -> None:
        publish_refill(self.room, self.session.userdata)

    async def on_enter(self) -> None:
        self.watch_transcript()
        await super().on_enter()


class HostedCity311(HostedGuard, City311Agent):
    demo = "city311"
    # Short turns in two languages: a reply per turn plus a ticket or a lookup.
    llm_budget = 16
    greeting = CITY311_GREETING

    def __init__(self) -> None:
        super().__init__()
        # Nova-3 in code-switching mode, and a voice that follows the router.
        self.update_options(stt=make_stt(), tts=make_tts())

    def initial_state(self) -> dict:
        return city311_state()

    def publish_initial(self) -> None:
        publish_city311(self.room, self.session.userdata)


class HostedCheckout(HostedGuard, VoiceCheckout):
    demo = "checkout"
    # Eleven fields, a read-back and the order: most answers are a tool call plus a reply.
    llm_budget = 24
    greeting = CHECKOUT_GREETING

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        # The checkout page owns the form; the agent only reaches it over RPC.
        pass


class HostedFurnace(HostedGuard, FurnaceLine):
    demo = "furnace"
    # Four details, two read-backs and a dispatch, often several per answer.
    llm_budget = 20
    detector_options = FURNACE_DETECTOR
    greeting = (
        f"Answer as the {FURNACE_COMPANY} after-hours line. Say this is a simulation: "
        "no technician is sent, so use a made-up address and number. "
        "Ask what is wrong with the heat."
    )

    def initial_state(self) -> dict:
        return furnace_state()

    def publish_initial(self) -> None:
        publish_furnace(self.room, self.session.userdata)
        self.watch_turns(self.session, self.session.vad)


# The playground registry: each STT, LLM and TTS demo the site can reserve.
# GPT-Live demos (REALTIME_DEMOS) start their own session below. Adding a demo here also
# needs its id in the site's PLAYGROUND_DEMOS and a COPY line in the Dockerfile.
CASCADE_AGENTS: dict[str, type[HostedGuard]] = {
    "coffee": HostedCoffee,
    "trivia": HostedTrivia,
    "water": HostedWater,
    "tenant": HostedTenant,
    "clinic": HostedClinic,
    "claim": HostedClaim,
    "panel": HostedPanel,
    "pharmacy": HostedPharmacy,
    "city311": HostedCity311,
    "checkout": HostedCheckout,
    "furnace": HostedFurnace,
}
# GPT-Live speech-to-speech demos share the call plumbing in build_server.
REALTIME_DEMOS = frozenset({"sdr", "interp"})
DEMOS = frozenset({*CASCADE_AGENTS, *REALTIME_DEMOS})


def realtime_model(demo: str):
    # Imported per call: each adapter loads its contributed demo by path.
    if demo == "interp":
        from hosted_interp import BACKEND_INSTRUCTIONS

        options = {"instructions": BACKEND_INSTRUCTIONS, "max_output_tokens": 100}
    else:
        from hosted_sdr import BACKEND_INSTRUCTIONS

        options = {"instructions": BACKEND_INSTRUCTIONS, "max_output_tokens": 600}
    return openai.realtime.GPTLiveModel(
        voice="marin",
        responses_options={
            "model": "gpt-5.6-luna",
            "parallel_tool_calls": False,
            "reasoning": {"effort": "low"},
            **options,
        },
    )


def realtime_agent(demo: str, *args):
    if demo == "interp":
        from hosted_interp import HostedInterpreter

        return HostedInterpreter(*args)
    from hosted_sdr import HostedSDR

    return HostedSDR(*args)


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
        if approval.get("demo") in REALTIME_DEMOS:
            demo = approval["demo"]
            claims.pop(ctx.room.name)
            session = AgentSession(
                llm=realtime_model(demo),
                vad=ctx.proc.userdata["vad"],
                conn_options=SessionConnectOptions(llm_conn_options=APIConnectOptions(max_retry=0)),
            )
            closing = False
            finished = asyncio.Event()
            sink = PlaygroundSink(approval["id"])

            async def finish_realtime():
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
                        logger.warning("Realtime final metering pending; reservation retained")
                    await control("finish", approval["id"])
                except (httpx.HTTPError, ValueError):
                    logger.warning("Realtime room cleanup pending server recovery")
                finally:
                    try:
                        await ctx.room.disconnect()
                    finally:
                        finished.set()

            sink.stop = finish_realtime

            async def deadline():
                await asyncio.sleep(max(0, approval["deadline"] - time.time()))
                await finish_realtime()

            timer = spawn(deadline())
            session.on("close", lambda _: spawn(finish_realtime()))
            agent = realtime_agent(demo, ctx.room, approval, finish_realtime, spawn, sink)
            session.on(
                "user_input_transcribed",
                lambda event: agent.hear(event.transcript) if event.is_final else None,
            )

            async def cleanup():
                timer.cancel()
                await finish_realtime()

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
        # Checkout visitors may publish data so the page can answer RPC. Never
        # let that channel feed typed chat to the model past the speech path.
        await session.start(
            agent=agent, room=ctx.room, record=False, room_options=RoomOptions(text_input=False)
        )

    return server


if __name__ == "__main__":
    cli.run_app(build_server())
