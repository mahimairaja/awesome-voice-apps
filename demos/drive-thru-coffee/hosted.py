"""Hosted coffee simulation: LiveKit worker with server-authorized call limits."""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from decimal import ROUND_CEILING, Decimal
from typing import ClassVar

import httpx
import status
import voicegateway
from agent import DriveThruAttendant, _publish_cart, _publish_menu
from hosted_approval import GREETING as APPROVAL_GREETING
from hosted_approval import HostedManager, RefundDesk, publish_approval
from hosted_approval import initial_state as approval_state
from hosted_approval import voice as approval_voice
from hosted_bill import GREETING as BILL_GREETING
from hosted_bill import BillExplainer
from hosted_bill import vision_cost as bill_vision_cost
from hosted_builder import GREETING as BUILDER_GREETING
from hosted_builder import VOICES, ConfigurableAgent, parse_config
from hosted_cancel import GREETING as CANCEL_GREETING
from hosted_cancel import CancelLine, publish_cancel
from hosted_cancel import initial_state as cancel_state
from hosted_cancel import instructions as cancel_instructions
from hosted_checkout import GREETING as CHECKOUT_GREETING
from hosted_checkout import VoiceCheckout
from hosted_city311 import GREETING as CITY311_GREETING
from hosted_city311 import City311Agent, publish_city311
from hosted_city311 import initial_state as city311_state
from hosted_city311 import make_stt as city311_stt
from hosted_city311 import make_tts as city311_tts
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
from hosted_copilot import GREETING as COPILOT_GREETING
from hosted_copilot import Prospect
from hosted_copilot import cost as copilot_cost
from hosted_copilot import limits as copilot_limits
from hosted_copilot import voice as copilot_voice
from hosted_cost import GREETING as COST_GREETING
from hosted_cost import CostRouter
from hosted_cost import router as cost_router
from hosted_deescalate import GREETING as DEESCALATE_GREETING
from hosted_deescalate import BillingDesk
from hosted_deescalate import voice as deescalate_voice
from hosted_delivery import DeliveryCaller, publish_delivery
from hosted_delivery import initial_state as delivery_state
from hosted_fraud import GREETING as FRAUD_GREETING
from hosted_fraud import FrontDesk, HostedFraudDesk, HostedVerify, publish_fraud
from hosted_fraud import initial_state as fraud_state
from hosted_fraud import voice as fraud_voice
from hosted_fraud import watch as fraud_watch
from hosted_furnace import COMPANY as FURNACE_COMPANY
from hosted_furnace import DETECTOR_OPTIONS as FURNACE_DETECTOR
from hosted_furnace import FurnaceLine, publish_furnace
from hosted_furnace import initial_state as furnace_state
from hosted_interview import InterviewDesk, Lobby, bounded_builder, order_for, round_seconds
from hosted_ivr import GREETING as IVR_GREETING
from hosted_ivr import PhoneTreeRouter, publish_router, router_cost
from hosted_ivr import initial_state as ivr_state
from hosted_ivr import instructions as ivr_instructions
from hosted_mortgage import GREETING as MORTGAGE_GREETING
from hosted_mortgage import TURN_HANDLING as MORTGAGE_TURN_HANDLING
from hosted_mortgage import MortgageAdvisor
from hosted_onprem import GREETING as ONPREM_GREETING
from hosted_onprem import INSTRUCTIONS as ONPREM_INSTRUCTIONS
from hosted_onprem import PrivateHealthLine
from hosted_onprem import initial_state as onprem_state
from hosted_outage import GREETING as OUTAGE_GREETING
from hosted_outage import OutageLine, outage_stt
from hosted_outage import initial_state as outage_state
from hosted_panel import GREETING as PANEL_GREETING
from hosted_panel import SoloPanelScribe, publish_panel
from hosted_payer import AGENT_VOICE as PAYER_VOICE
from hosted_payer import PayerCaller, publish_payer
from hosted_payer import initial_state as payer_state
from hosted_payer import instructions as payer_instructions
from hosted_pharmacy import GREETING as PHARMACY_GREETING
from hosted_pharmacy import RefillLine, publish_refill
from hosted_pharmacy import initial_state as pharmacy_state
from hosted_pharmacy import instructions as pharmacy_instructions
from hosted_pharmacy import make_stt as pharmacy_stt
from hosted_postop import CheckInCall, publish_checkin
from hosted_postop import greeting as postop_greeting
from hosted_postop import initial_state as postop_state
from hosted_postop import instructions as postop_instructions
from hosted_postop import make_stt as postop_stt
from hosted_postop import make_tts as postop_tts
from hosted_pronounce import GREETING as PRONOUNCE_GREETING
from hosted_pronounce import PronunciationCoach, publish_coach
from hosted_pronounce import initial_state as pronounce_state
from hosted_pronounce import instructions as pronounce_instructions
from hosted_pronounce import make_stt as pronounce_stt
from hosted_pronounce import make_tts as pronounce_tts
from hosted_rebook import FlightRebooker, build_llm, build_tts
from hosted_recall import HOSTED_INSTRUCTIONS as RECALL_INSTRUCTIONS
from hosted_recall import Concierge, publish_recall, summary_cost
from hosted_recall import SiteStore as RecallStore
from hosted_recall import greeting as recall_greeting
from hosted_recall import initial_state as recall_state
from hosted_resume import LoanCallback
from hosted_resume import SiteStore as ResumeStore
from hosted_resume import instructions as resume_instructions
from hosted_returns import GRADE_BUDGET, ReturnsDesk, grade_cost, publish_returns
from hosted_returns import GREETING as RETURNS_GREETING
from hosted_returns import initial_state as returns_state
from hosted_router import GREETING as ROUTER_GREETING
from hosted_router import RouterRescue, vision_cost
from hosted_stresstest import GREETING as STRESSTEST_GREETING
from hosted_stresstest import SIM_USD_CAP, StressTestLead, sim_cost
from hosted_stresstest import initial_state as stresstest_state
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


# Transcribed words a caller must say over the agent before it stops talking.
INTERRUPT_MIN_WORDS = 2


class HostedGuard:
    """Call limits shared by every STT, LLM and TTS demo.

    A demo mixes this in front of its contributed agent and supplies the
    per-call state, the first UI events, and the opening instruction.
    """

    demo = "coffee"
    greeting = ""
    # LLM requests allowed in a full two-minute call; shorter calls scale down.
    llm_budget = 12
    # Session turn-taking; a demo about interruptions brings its own. Raw voice activity
    # alone (a cough, a TV, the agent's own voice leaking back through laptop speakers)
    # used to cut a reply after its first word. Now it takes two transcribed words to
    # interrupt, and a stray sound that never becomes words lets the reply resume.
    turn_handling = {
        "turn_detection": "vad",
        "interruption": {
            "mode": "vad",
            "min_duration": 0.6,
            "min_words": INTERRUPT_MIN_WORDS,
            "resume_false_interruption": True,
            "false_interruption_timeout": 1.5,
        },
    }

    def initial_state(self) -> dict:
        raise NotImplementedError

    def publish_initial(self) -> None:
        raise NotImplementedError

    def voice_stack(self) -> dict:
        """The metered cloud stack. A demo that wraps its own clients returns those."""
        return {
            "stt": deepgram.STT(model="nova-3"),
            "llm": openai.LLM(
                model="gpt-4o-mini", max_completion_tokens=180, max_retries=0, store=False
            ),
            "tts": cartesia.TTS(model="sonic-2"),
        }

    def __init__(self) -> None:
        ctx = get_job_context()
        self.approval = claims.pop(ctx.room.name, None)
        if not self.approval:
            raise RuntimeError("Call has no approved reservation")
        super().__init__(ctx.room)
        # Per-agent clients keep component metrics isolated between concurrent calls.
        self.update_options(**self.voice_stack())
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
            # Outbound demos wait for the callee to speak first.
            if self.greeting:
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
        self.update_options(stt=pharmacy_stt())

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
        self.update_options(stt=city311_stt(), tts=city311_tts())

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


class HostedFraud(HostedGuard, FrontDesk):
    """Front desk of a multi-agent call: verification and the specialist share its caps."""

    demo = "fraud"
    # Three checks, a handoff, an action and a few manipulation attempts.
    llm_budget = 28
    greeting = FRAUD_GREETING

    def __init__(self) -> None:
        super().__init__()
        self.update_options(tts=fraud_voice("front"))

    def initial_state(self) -> dict:
        return fraud_state()

    def publish_initial(self) -> None:
        publish_fraud(self.room, self.session.userdata)

    async def on_enter(self) -> None:
        fraud_watch(self.session, self.room)
        await super().on_enter()

    def make_verifier(self):
        return HostedVerify(self, self.room, stt=self.stt, llm=self.llm, tts=self.tts)

    def make_fraud_desk(self, case: dict):
        return HostedFraudDesk(
            self,
            self.room,
            case,
            chat_ctx=self.chat_ctx.copy(
                exclude_function_call=True, exclude_instructions=True
            ).truncate(max_items=6),
            stt=self.stt,
            llm=self.llm,
            tts=fraud_voice("specialist"),
        )

    # The same limits as HostedGuard.llm_node and tts_node, on the shared
    # counters, for the agents this call hands off to.
    def charge_llm(self, chat_ctx) -> bool:
        self._llm_requests += 1
        scale = self.approval["seconds"] / 120
        if (
            self._llm_requests > max(1, int(self.llm_budget * scale))
            or len(json.dumps(chat_ctx.to_dict()).encode()) > 16000
        ):
            spawn(self._finish())
            return False
        return True

    async def bound_text(self, text):
        async for chunk in text:
            self._tts_bytes += len(chunk.encode())
            if self._tts_bytes > int(4000 * self.approval["seconds"] / 120):
                spawn(self._finish())
                return
            yield chunk


class HostedInterview(HostedGuard, Lobby):
    """Blind A/B interview: a pipeline and GPT-Live take one round each."""

    demo = "interview"
    # The lobby hands off at once; each interviewer opens its own round.
    greeting = ""
    desk = None

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        # GPT-Live bills by the session minute, so cap the call like the SDR demo.
        self.sink.stop = self._finish
        self.desk = InterviewDesk(
            self.session,
            self.room,
            order_for(self.approval["id"]),
            build=bounded_builder(self._finish, spawn),
            on_done=self._wrap_up,
            round_seconds=round_seconds(self.approval["seconds"]),
        )
        self.session.update_agent(self.desk.first_agent())

    async def _wrap_up(self) -> None:
        # Let the closing line play, then end early: the vote happens on the page.
        await asyncio.sleep(3)
        await self._finish()

    async def _finish(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._deadline_task and self._deadline_task is not asyncio.current_task():
            self._deadline_task.cancel()
        if self.desk:
            self.desk.close()
        session = self._active_session
        session.shutdown(drain=False)
        try:
            # GPT-Live reports its final duration on close. Meter it before the
            # room goes, so the reservation is charged for the whole session.
            async with asyncio.timeout(8):
                await session.aclose()
                capture = getattr(session, "_vg_capture", None)
                if capture:
                    await capture.reconcile(session)
                    await capture.drain()
                await self.sink.flush()
        except Exception:  # noqa: BLE001 - the site keeps the reservation on failure
            logger.warning("Interview final metering pending; reservation retained")
        try:
            await control("finish", self.approval["id"])
        except (httpx.HTTPError, ValueError):
            logger.warning("Room cleanup pending server recovery")
        finally:
            await self.room.disconnect()


class HostedMortgage(HostedGuard, MortgageAdvisor):
    demo = "mortgage"
    # The advisor talks in paragraphs and every barge-in is a new reply.
    llm_budget = 16
    greeting = MORTGAGE_GREETING
    # VAD never pauses the agent here; the demo's backchannel gate decides.
    turn_handling = MORTGAGE_TURN_HANDLING

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        self.watch(self.session)
        MortgageAdvisor.publish_initial(self)


class HostedRouter(HostedGuard, RouterRescue):
    demo = "router"
    # Each look is a tool call plus a reply, on top of the usual turns.
    llm_budget = 20
    greeting = ROUTER_GREETING

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        RouterRescue.publish_initial(self)

    def meter_vision(self, usage) -> None:
        # Direct vision requests bypass VoiceGateway; bill each look to this call.
        self.sink.records[f"router-vision-{uuid.uuid4()}"] = ("openai", vision_cost(usage))
        spawn(self.sink.report())


class HostedBuilder(HostedGuard, ConfigurableAgent):
    """The visitor's own agent: config from the site's dispatch, live updates over RPC."""

    demo = "builder"
    greeting = BUILDER_GREETING
    # Visitors re-test after each change, so allow a few more turns than a fixed demo.
    llm_budget = 16

    def __init__(self) -> None:
        super().__init__()
        self.update_options(tts=cartesia.TTS(model="sonic-2", voice=VOICES[self.config.voice]))

    def initial_config(self):
        # The site validated this config and signed it into the dispatch; check it again.
        try:
            raw = json.loads(get_job_context().job.metadata).get("config")
            return parse_config(raw)
        except (ValueError, TypeError, AttributeError):
            logger.warning("Builder call without a valid config; using the default")
            return super().initial_config()

    def allowed_caller(self, identity: str) -> bool:
        # Only this call's visitor may reconfigure this call's agent.
        return identity == f"visitor-{self.approval['id']}"

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        self.publish_config()

    async def on_enter(self) -> None:
        self.listen()
        await super().on_enter()


class HostedRebook(HostedGuard, FlightRebooker):
    demo = "rebook"
    # A background search adds a reply for its update and one for its result.
    llm_budget = 20
    greeting = (
        "Say this is a rebooking simulation for a made-up airline and nothing is booked. "
        "Say their 18:05 flight to Vancouver is cancelled for weather and offer to rebook."
    )

    def __init__(self) -> None:
        super().__init__()
        # Primary plus fallback, both inside the providers the playground meters.
        replaced = (self.llm, self.tts)
        failover_llm = build_llm(self.outage, max_completion_tokens=180, max_retries=0, store=False)
        failover_tts = build_tts(self.outage)
        self.update_options(llm=failover_llm, tts=failover_tts)
        self.watch(failover_llm, failover_tts)
        for component in replaced:
            spawn(component.aclose())

    def initial_state(self) -> dict:
        return self.state

    def publish_initial(self) -> None:
        self.publish()

    async def on_enter(self) -> None:
        await super().on_enter()
        if not self._closing:
            await self.start_background(self.session)


class HostedReturns(HostedGuard, ReturnsDesk):
    demo = "returns"
    # Verify, check options and issue: each is a tool call plus a reply.
    llm_budget = 20
    greeting = RETURNS_GREETING

    def __init__(self) -> None:
        super().__init__()
        self.qa.max_grades = max(1, int(GRADE_BUDGET * self.approval["seconds"] / 120))

    def initial_state(self) -> dict:
        return returns_state()

    def publish_initial(self) -> None:
        publish_returns(self.room, self.session.userdata, self.qa)

    async def on_enter(self) -> None:
        # Grade from the greeting on; the guard then starts the call as usual.
        self.qa.attach(self.session)
        await super().on_enter()

    def record_qa_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        # VoiceGateway does not see the grader's direct OpenAI calls; bill them here.
        sink = getattr(self, "sink", None)
        if sink is None:
            return
        cost = grade_cost(prompt_tokens, completion_tokens)
        sink.records[f"returns-qa-{uuid.uuid4()}"] = ("openai", cost)
        spawn(sink.report())


class HostedDelivery(HostedGuard, DeliveryCaller):
    demo = "delivery"
    # No greeting: the agent places the call and listens for who answers first.
    greeting = ""

    def initial_state(self) -> dict:
        return delivery_state()

    def publish_initial(self) -> None:
        publish_delivery(self.room, self.session.userdata)

    async def on_enter(self) -> None:
        await super().on_enter()
        self._call = spawn(self.run_call())

    async def hang_up(self) -> None:
        if self._hung_up:
            return
        self._hung_up = True
        # The site deletes the room when the call ends.
        await self._finish()


class HostedOutage(HostedGuard, OutageLine):
    demo = "outage"
    # A ticket, two or three line changes, a reply after each reading and a playback.
    llm_budget = 20
    greeting = OUTAGE_GREETING

    def __init__(self) -> None:
        super().__init__()
        # Formatted numbers keep readings comparable with the card on screen.
        self.update_options(stt=outage_stt())

    def initial_state(self) -> dict:
        return outage_state()

    def publish_initial(self) -> None:
        self.install_line()
        self.publish()


class HostedCancel(HostedGuard, CancelLine):
    demo = "cancel"
    # Reason, offer and a decision, each a tool call plus a short reply. When the
    # policy cancels on its own it speaks without an LLM request at all.
    llm_budget = 20
    greeting = CANCEL_GREETING

    def __init__(self) -> None:
        super().__init__()
        self._instructions = cancel_instructions()
        # Sonic 2 retires on 2026-10-20.
        self.update_options(tts=cartesia.TTS(model="sonic-3"))

    def initial_state(self) -> dict:
        return cancel_state()

    def publish_initial(self) -> None:
        publish_cancel(self.room, self.session.userdata)


class HostedBill(HostedGuard, BillExplainer):
    demo = "bill"
    # Each bill read adds a tool call plus a reply on top of the usual turns.
    llm_budget = 24
    greeting = BILL_GREETING

    def __init__(self) -> None:
        super().__init__()
        # sonic-2 is being retired; this demo starts on sonic-3.
        self.update_options(tts=cartesia.TTS(model="sonic-3"))

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        BillExplainer.publish_initial(self)

    async def on_enter(self) -> None:
        self.watch_uploads()
        await super().on_enter()

    def meter_vision(self, usage) -> None:
        # Direct vision requests bypass VoiceGateway; bill each read to this call.
        self.sink.records[f"bill-vision-{uuid.uuid4()}"] = ("openai", bill_vision_cost(usage))
        spawn(self.sink.report())


class HostedPronounce(HostedGuard, PronunciationCoach):
    demo = "pronounce"
    # Every read is a scored turn plus a coaching reply: about ten reads a call.
    llm_budget = 20
    greeting = PRONOUNCE_GREETING

    def __init__(self) -> None:
        super().__init__()
        self._instructions = pronounce_instructions()
        # Plain Nova-3 (no keyterms, so errors stay visible) and multilingual Sonic 3.
        self.update_options(stt=pronounce_stt(), tts=pronounce_tts())

    def initial_state(self) -> dict:
        return pronounce_state()

    def publish_initial(self) -> None:
        publish_coach(self.room, self.session.userdata)


class HostedPostop(HostedGuard, CheckInCall):
    demo = "postop"
    # Five questions, each a record call plus a reply, then the outcome.
    llm_budget = 26

    def __init__(self) -> None:
        super().__init__()
        self._instructions = postop_instructions()
        self.update_options(stt=postop_stt(), tts=postop_tts())

    @property
    def greeting(self) -> str:
        return postop_greeting(self.session.userdata)

    def initial_state(self) -> dict:
        return postop_state()

    def publish_initial(self) -> None:
        publish_checkin(self.room, self.session.userdata)


class HostedApproval(HostedGuard, RefundDesk):
    """Returns agent that asks the visitor, as manager, to approve over RPC."""

    demo = "approval"
    # Lookup, the request, a little hold talk, the decision, the refund and
    # possibly a transfer: most turns are a tool call plus a reply.
    llm_budget = 24
    greeting = APPROVAL_GREETING

    def __init__(self) -> None:
        super().__init__()
        self.update_options(tts=approval_voice("agent"))

    def initial_state(self) -> dict:
        return approval_state()

    def publish_initial(self) -> None:
        publish_approval(self.room, self.session.userdata)

    def make_manager(self, case: dict):
        return HostedManager(
            self,
            self.room,
            case,
            chat_ctx=self._carried_context(),
            stt=self.stt,
            llm=self.llm,
            tts=approval_voice("manager"),
        )

    # The same limits as HostedGuard.llm_node and tts_node, on the shared
    # counters, for the manager this call transfers to.
    def charge_llm(self, chat_ctx) -> bool:
        self._llm_requests += 1
        scale = self.approval["seconds"] / 120
        if (
            self._llm_requests > max(1, int(self.llm_budget * scale))
            or len(json.dumps(chat_ctx.to_dict()).encode()) > 16000
        ):
            spawn(self._finish())
            return False
        return True

    async def bound_text(self, text):
        async for chunk in text:
            self._tts_bytes += len(chunk.encode())
            if self._tts_bytes > int(4000 * self.approval["seconds"] / 120):
                spawn(self._finish())
                return
            yield chunk


class HostedDeescalate(HostedGuard, BillingDesk):
    demo = "deescalate"
    # Every caller turn is a reply, and explaining or crediting a charge adds a tool call.
    llm_budget = 24
    greeting = DEESCALATE_GREETING

    def __init__(self) -> None:
        super().__init__()
        self.update_options(tts=deescalate_voice())

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        self.watch(self.session)
        BillingDesk.publish_initial(self)

    def end_call(self) -> None:
        # The transfer ends the reservation, not just the session.
        spawn(self._finish())


class HostedRecall(HostedGuard, Concierge):
    demo = "recall"
    # Consent, a few notes and a forget: each one is a tool call plus a reply.
    llm_budget = 20
    # First-call greeting; on_enter swaps in a welcome back when the file has notes.
    greeting = recall_greeting([], 0)
    base_instructions = Concierge.base_instructions + RECALL_INSTRUCTIONS

    def __init__(self) -> None:
        super().__init__()
        # The client file lives on the site, keyed by the reservation's account.
        self.store = RecallStore(control, self.approval["id"])
        self.update_options(tts=cartesia.TTS(model="sonic-3"))
        self._entries: list[dict] = []

    def initial_state(self) -> dict:
        return recall_state(self._entries)

    def publish_initial(self) -> None:
        self.watch_transcript()
        publish_recall(self.room, self.session.userdata)

    async def on_enter(self) -> None:
        try:
            self._entries = await self.load_file()
        except (httpx.HTTPError, ValueError, KeyError):
            logger.warning("Client file unavailable; starting with no memory")
        self.greeting = recall_greeting(self._entries, time.time())
        get_job_context().add_shutdown_callback(self._wrap_up)
        await super().on_enter()

    async def _wrap_up(self, _reason: str = "") -> None:
        await self.write_summary()

    def meter_summary(self, prompt_tokens: int, completion_tokens: int) -> None:
        # VoiceGateway does not see the post-call summary request; bill it here.
        cost = summary_cost(prompt_tokens, completion_tokens)
        self.sink.records[f"recall-summary-{uuid.uuid4()}"] = ("openai", cost)

    async def _finish(self) -> None:
        if not self._closing:
            # Stop speaking, then summarise while the reservation still accepts writes.
            self._active_session.shutdown(drain=False)
            await self.write_summary()
            try:
                await self.sink.report()
            except (httpx.HTTPError, ValueError):
                logger.warning("Summary cost report pending")
        await super()._finish()


class HostedCost(HostedGuard, CostRouter):
    demo = "cost"
    # Cache hits and tool steps also count as requests here, so the cap is
    # higher than a plain demo's; each tier's own client carries the token caps.
    llm_budget = 30
    greeting = COST_GREETING
    llm_options: ClassVar[dict] = {"max_completion_tokens": 180, "max_retries": 0, "store": False}

    def __init__(self) -> None:
        super().__init__()
        self.update_options(tts=cartesia.TTS(model="sonic-3"))
        self._unbilled_tokens = 0

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        self.publish()

    async def on_enter(self) -> None:
        await self.load_index()
        await super().on_enter()
        self.meter_embedding(0)

    def meter_embedding(self, tokens: int) -> None:
        # VoiceGateway does not see direct embedding calls; bill them here.
        # The index is built before the sink exists, so hold those tokens.
        sink = getattr(self, "sink", None)
        self._unbilled_tokens += max(0, tokens)
        if sink is None or not self._unbilled_tokens:
            return
        cost = Decimal(self._unbilled_tokens) * cost_router.EMBED_PRICE / 1_000_000
        self._unbilled_tokens = 0
        sink.records[f"cost-embedding-{uuid.uuid4()}"] = ("openai", cost)
        spawn(sink.report())


class HostedPayer(HostedGuard, PayerCaller):
    demo = "payer"
    # No greeting: the agent places the call, works the menu, and waits on hold.
    greeting = ""
    # Three menu choices, a few hold checks, then a tool call and a reply per answer.
    llm_budget = 36

    def __init__(self) -> None:
        super().__init__()
        self._instructions = payer_instructions()
        # sonic-3: Cartesia retires sonic-2. The phone tree switches voices per prompt.
        self.update_options(tts=cartesia.TTS(model="sonic-3", voice=PAYER_VOICE))

    def initial_state(self) -> dict:
        return payer_state()

    def publish_initial(self) -> None:
        publish_payer(self.room, self.session.userdata)

    async def on_enter(self) -> None:
        await super().on_enter()
        self._call = spawn(self.run_call())

    def allow_side_call(self) -> bool:
        # Menu choices and hold checks bypass llm_node, so count them here.
        self._llm_requests += 1
        if self._llm_requests > max(1, int(self.llm_budget * self.approval["seconds"] / 120)):
            spawn(self._finish())
            return False
        return True

    async def hang_up(self) -> None:
        if self._hung_up:
            return
        self._hung_up = True
        await self._finish()


class HostedResume(HostedGuard, LoanCallback):
    demo = "resume"
    # Replaced per call in on_enter: a fresh opening, or a welcome back.
    greeting = "Ask the first question of the loan application."
    # Seven answers over two or more short calls: each is a tool call and a reply.
    llm_budget = 24

    def __init__(self) -> None:
        super().__init__()
        self._instructions = resume_instructions()
        # Checkpoints go to the site, keyed by the account behind this reservation.
        self.store = ResumeStore(control, self.approval["id"])
        # A fresh per-call client; sonic-3, since sonic-2 is being retired.
        self.update_options(tts=cartesia.TTS(model="sonic-3"))

    def initial_state(self) -> dict:
        return self.state

    def publish_initial(self) -> None:
        self.publish()

    async def on_enter(self) -> None:
        # Load the checkpoint before the greeting, so the first words can resume.
        await self.restore()
        self.greeting = self.opening()
        self.watch()
        await super().on_enter()


class HostedOnprem(HostedGuard, PrivateHealthLine):
    """A trial check-in where the language model never receives the participant number.

    The demo wraps its own OpenAI client to measure and inspect every model
    request, so the hosted copy keeps the demo's stack instead of the default.
    """

    demo = "onprem"
    # Five answers, each a tool call and a reply, plus the read-out at the end.
    llm_budget = 20
    greeting = ONPREM_GREETING

    def __init__(self) -> None:
        super().__init__()
        self._instructions = ONPREM_INSTRUCTIONS

    def voice_stack(self) -> dict:
        return self.stack

    def initial_state(self) -> dict:
        state = onprem_state()
        self.bind(state)
        return state

    def publish_initial(self) -> None:
        self.publish()

    async def _finish(self) -> None:
        try:
            await super()._finish()
        finally:
            await self.aclose_clients()


class HostedIvr(HostedGuard, PhoneTreeRouter):
    demo = "ivr"
    # A clear request is one reply plus the transfer; leave room for clarifying
    # questions and a second or third request in the same call.
    llm_budget = 20
    greeting = IVR_GREETING

    def __init__(self) -> None:
        super().__init__()
        self._instructions = ivr_instructions()
        self.update_options(tts=cartesia.TTS(model="sonic-3"))

    def initial_state(self) -> dict:
        # Both race clocks start here, when the call connects.
        return ivr_state()

    def publish_initial(self) -> None:
        publish_router(self.room, self.session.userdata)

    async def on_enter(self) -> None:
        self.watch()
        await super().on_enter()

    def meter_router(self, prompt_tokens: int, completion_tokens: int) -> None:
        # VoiceGateway does not see the direct router call; bill it here.
        cost = router_cost(prompt_tokens, completion_tokens)
        self.sink.records[f"ivr-router-{uuid.uuid4()}"] = ("openai", cost)
        spawn(self.sink.report())


class HostedCopilot(HostedGuard, Prospect):
    """The prospect speaks through HostedGuard; the silent copilot bills to the same call."""

    demo = "copilot"
    greeting = COPILOT_GREETING

    def __init__(self) -> None:
        super().__init__()
        self.update_options(tts=copilot_voice())

    def initial_state(self) -> dict:
        return {}

    def publish_initial(self) -> None:
        self.start_copilot(meter=self.meter_copilot, **copilot_limits(self.approval["seconds"]))

    def meter_copilot(self, model: str, input_tokens: int, output_tokens: int) -> None:
        # VoiceGateway does not see the copilot's direct OpenAI calls; bill them here.
        cost = copilot_cost(model, input_tokens, output_tokens)
        self.sink.records[f"copilot-{uuid.uuid4()}"] = ("openai", cost)
        spawn(self.sink.report())


class HostedStressTest(HostedGuard, StressTestLead):
    demo = "stresstest"
    # The lead's own turns plus a live test call; simulations are metered below.
    llm_budget = 24
    greeting = STRESSTEST_GREETING

    def __init__(self) -> None:
        super().__init__()
        self.update_options(tts=cartesia.TTS(model="sonic-3"))
        self._sim_usd = Decimal(0)
        self._report_pending = False

    def initial_state(self) -> dict:
        return stresstest_state()

    def publish_initial(self) -> None:
        self.publish()

    async def on_enter(self) -> None:
        self.watch_live()
        await super().on_enter()

    def make_sim_llm(self):
        # One client per suite: thread jobs run separate event loops.
        model = openai.LLM(
            model="gpt-4o-mini", max_completion_tokens=220, max_retries=0, store=False
        )
        model.on("metrics_collected", self.meter_simulation)
        return model

    def can_spend(self) -> bool:
        return self._sim_usd < SIM_USD_CAP

    def meter_simulation(self, metrics) -> None:
        # VoiceGateway meters the voice session only; bill simulated calls here.
        cost = sim_cost(metrics)
        self._sim_usd += cost
        self.sink.records[f"stresstest-sim-{uuid.uuid4()}"] = ("openai", cost)
        if not self._report_pending:
            # Dozens of requests finish together: one usage report per second.
            self._report_pending = True
            spawn(self._report_simulation())

    async def _report_simulation(self) -> None:
        await asyncio.sleep(1)
        self._report_pending = False
        try:
            await self.sink.report()
        except (httpx.HTTPError, ValueError):
            logger.warning("Simulation usage report pending")


class HostedConcierge(HostedGuard, HotelConcierge):
    """A concierge with a face: a Spatius avatar speaks the agent's audio.

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
        from livekit.plugins import spatius

        self._meter.attach(session)
        self._avatar_started = time.monotonic()
        try:
            # Reads SPATIUS_API_KEY, SPATIUS_APP_ID and SPATIUS_AVATAR_ID; a missing
            # key raises here and ends the call like a failed join.
            avatar = spatius.AvatarSession(avatar_participant_name=CONCIERGE)
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
            "service": "spatius",
            "microusd": int((cost * 1_000_000).to_integral_value(rounding=ROUND_CEILING)),
        }

    def bill_avatar(self) -> None:
        sink = getattr(self, "sink", None)
        if sink is not None and self._avatar_started is not None:
            sink.records["concierge-avatar"] = (
                "spatius",
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
    "fraud": HostedFraud,
    "interview": HostedInterview,
    "mortgage": HostedMortgage,
    "router": HostedRouter,
    "builder": HostedBuilder,
    "rebook": HostedRebook,
    "returns": HostedReturns,
    "delivery": HostedDelivery,
    "outage": HostedOutage,
    "cancel": HostedCancel,
    "bill": HostedBill,
    "pronounce": HostedPronounce,
    "postop": HostedPostop,
    "approval": HostedApproval,
    "deescalate": HostedDeescalate,
    "recall": HostedRecall,
    "cost": HostedCost,
    "payer": HostedPayer,
    "resume": HostedResume,
    "onprem": HostedOnprem,
    "ivr": HostedIvr,
    "copilot": HostedCopilot,
    "stresstest": HostedStressTest,
    "concierge": HostedConcierge,
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
        demo = CASCADE_AGENTS[claims.get(ctx.room.name, {}).get("demo", "coffee")]
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
            turn_handling=demo.turn_handling,
        )
        await ctx.connect()
        agent = demo()
        # Avatar demos route the session's audio to a video participant first.
        if hasattr(agent, "start_avatar"):
            await agent.start_avatar(session)
        # Checkout and approval visitors may publish data so the page can answer RPC. Never
        # let that channel feed typed chat to the model past the speech path.
        await session.start(
            agent=agent, room=ctx.room, record=False, room_options=RoomOptions(text_input=False)
        )

    return server


if __name__ == "__main__":
    server = build_server()
    if sys.argv[1:2] == ["start"]:
        # Liveness and key checks for mahimai.ca/status; only on the hosted worker.
        status.start(DEMOS)
    cli.run_app(server)
