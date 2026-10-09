"""interview-coach: a blind A/B mock interview.

Two interviewers each ask one question and one follow-up. One is a classic
pipeline (Deepgram STT, an OpenAI LLM, Cartesia TTS). The other is GPT-Live, a
full-duplex model that listens while it talks. The candidate does not know
which is which until they vote.

Every reply is timed from the end of the candidate's speech to the first audio
of the answer. Pipeline turns also report each stage: end of turn,
transcription, first token and first audio.

Run it:
1. Copy .env.example to .env and fill in the keys.
2. uv sync
3. uv run python agent.py download-files
4. uv run python agent.py console
"""

import asyncio
import json
import logging
import random
import time

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger("interview-coach")

QUESTIONS = (
    "Tell me about a project you are proud of. What was your part in it?",
    "Tell me about a time something you built broke. What did you do next?",
)
LABELS = ("A", "B")
# One question and one follow-up per interviewer.
ANSWERS_PER_ROUND = 2
# A round closes at the next reply after this, and is cut after the grace period.
ROUND_SECONDS = 50.0
ROUND_GRACE = 12.0
# Bounds model work if a round never reaches its answers.
MAX_REPLIES_PER_ROUND = 6
ARCHITECTURES = ("cascade", "duplex")


def instructions(label: str, question: str) -> str:
    return f"""You are Interviewer {label} in a short practice interview for a software role.
Nothing is recorded or scored. The candidate is comparing two interviewers blind, so
never say what technology you run on, and never mention pipelines, models or latency.
Your question is: "{question}"
After the candidate's first answer, react in one short sentence that names a detail
they said, then ask one short follow-up about it. After their second answer, thank
them in one sentence and stop. Keep every turn under 25 words. Do not grade or coach."""


def opening(label: str, question: str) -> str:
    return f"Say you are Interviewer {label}, then ask exactly: {question}"


class CascadeInterviewer(Agent):
    """STT, LLM and TTS in sequence: the turn ends before the reply starts."""

    architecture = "cascade"

    def __init__(self, label: str, question: str, *, stt=None, llm=None, tts=None) -> None:
        super().__init__(
            instructions=instructions(label, question),
            stt=stt or deepgram.STT(model="nova-3"),
            llm=llm or openai.LLM(model="gpt-4o-mini", max_completion_tokens=120),
            tts=tts or cartesia.TTS(model="sonic-2"),
            turn_handling={"turn_detection": "vad"},
        )
        self.seat = label
        self.question = question

    async def on_enter(self) -> None:
        await self.session.generate_reply(instructions=opening(self.seat, self.question))


class DuplexInterviewer(Agent):
    """GPT-Live hears the candidate while it speaks and takes turns itself."""

    architecture = "duplex"

    def __init__(self, label: str, question: str, *, model=None) -> None:
        super().__init__(
            instructions=instructions(label, question),
            llm=model
            or openai.realtime.GPTLiveModel(
                voice="marin",
                responses_options={"max_output_tokens": 200, "reasoning": {"effort": "low"}},
            ),
            turn_handling={"turn_detection": "realtime_llm"},
        )
        self.seat = label
        self.question = question

    async def on_enter(self) -> None:
        await self.session.generate_reply(instructions=opening(self.seat, self.question))


def make_interviewer(architecture: str, label: str, question: str) -> Agent:
    if architecture == "duplex":
        return DuplexInterviewer(label, question)
    return CascadeInterviewer(label, question)


def _ms(seconds) -> int | None:
    if not isinstance(seconds, (int, float)) or seconds < 0 or seconds > 30:
        return None
    return round(seconds * 1000)


class InterviewDesk:
    """Runs the two rounds, times every reply and publishes it on the "ui" topic.

    Session events outlive agent handoffs, so one desk sees both interviewers.
    """

    def __init__(
        self,
        session: AgentSession,
        room: rtc.Room,
        order: tuple[str, str],
        *,
        build=make_interviewer,
        on_done=None,
        round_seconds: float = ROUND_SECONDS,
    ) -> None:
        if sorted(order) != sorted(ARCHITECTURES):
            raise ValueError("Each architecture interviews exactly once")
        self.session = session
        self.room = room
        self.order = order
        self.build = build
        self.on_done = on_done
        self.round_seconds = round_seconds
        self.round = 0
        self.answers = 0
        self.replies = 0
        self.turns = 0
        self.done = False
        self.round_started = time.monotonic()
        self.user_speaking = False
        self.user_stopped: float | None = None
        self.gap: float | None = None
        self.overlap = False
        self.user_metrics: dict = {}
        self._tasks: set[asyncio.Task] = set()
        session.on("user_state_changed", self._on_user_state)
        session.on("agent_state_changed", self._on_agent_state)
        session.on("conversation_item_added", self._on_item)
        self._spawn(self._watch())

    @property
    def label(self) -> str:
        return LABELS[self.round]

    @property
    def architecture(self) -> str:
        return self.order[self.round]

    def first_agent(self) -> Agent:
        self.publish_round()
        return self.build(self.order[0], LABELS[0], QUESTIONS[0])

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def publish(self, component: str, props: dict) -> None:
        payload = json.dumps({"type": "ui_event", "component": component, "props": props})

        async def send():
            try:
                await self.room.local_participant.publish_data(
                    payload.encode(), reliable=True, topic="ui"
                )
            except Exception:  # noqa: BLE001 - the interview continues without the panel
                logger.warning("Could not publish %s", component)

        self._spawn(send())

    def publish_round(self) -> None:
        self.publish(
            "Interview",
            {
                "round": self.round + 1,
                "label": self.label,
                "question": QUESTIONS[self.round],
                "answers": min(self.answers, ANSWERS_PER_ROUND),
                "of": ANSWERS_PER_ROUND,
                "status": "done" if self.done else "interviewing",
            },
        )

    def _on_user_state(self, event) -> None:
        if event.new_state == "speaking":
            self.user_speaking = True
        elif event.old_state == "speaking":
            self.user_speaking = False
            self.user_stopped = time.monotonic()

    def _on_agent_state(self, event) -> None:
        if event.new_state != "speaking":
            return
        # A duplex model may start talking before the candidate stops.
        self.overlap = self.user_speaking
        self.gap = 0.0 if self.overlap else None
        if self.user_stopped is not None and not self.overlap:
            self.gap = time.monotonic() - self.user_stopped
        self.user_stopped = None

    def _on_item(self, event) -> None:
        item = event.item
        role = getattr(item, "role", None)
        if self.done or role not in {"user", "assistant"}:
            return
        metrics = getattr(item, "metrics", None) or {}
        if role == "user":
            if (getattr(item, "text_content", "") or "").strip():
                self.answers += 1
                self.user_metrics = metrics
                self.publish_round()
            return
        self.replies += 1
        if self.answers and self.user_metrics is not None:
            self.publish_turn(metrics)
        self.user_metrics = None
        elapsed = time.monotonic() - self.round_started
        if (
            self.answers >= ANSWERS_PER_ROUND
            or elapsed > self.round_seconds
            or self.replies >= MAX_REPLIES_PER_ROUND
        ):
            self.advance()

    def publish_turn(self, metrics: dict) -> None:
        user = self.user_metrics or {}
        total = _ms(metrics.get("e2e_latency"))
        if total is None:
            total = _ms(self.gap)
        stages = {}
        if self.architecture == "cascade":
            stages = {
                "endpoint": _ms(user.get("end_of_turn_delay")),
                "stt": _ms(user.get("transcription_delay")),
                "llm": _ms(metrics.get("llm_node_ttft")),
                "tts": _ms(metrics.get("tts_node_ttfb")),
            }
            stages = {key: value for key, value in stages.items() if value is not None}
        self.turns += 1
        self.publish(
            "Turn",
            {
                "label": self.label,
                "turn": self.turns,
                "total_ms": total,
                "stages": stages,
                "overlap": self.overlap,
            },
        )

    def advance(self) -> None:
        if self.done:
            return
        if self.round == 0:
            self.round = 1
            self.answers = 0
            self.replies = 0
            self.user_metrics = {}
            self.round_started = time.monotonic()
            self.session.update_agent(self.build(self.order[1], LABELS[1], QUESTIONS[1]))
            self.publish_round()
            return
        self.done = True
        self.publish_round()
        self.publish("Reveal", dict(zip(LABELS, self.order)))
        if self.on_done:
            self._spawn(self.on_done())

    async def _watch(self) -> None:
        while not self.done:
            await asyncio.sleep(1)
            if time.monotonic() - self.round_started > self.round_seconds + ROUND_GRACE:
                self.advance()

    def close(self) -> None:
        for task in list(self._tasks):
            if task is not asyncio.current_task():
                task.cancel()


server = AgentServer()


@server.rtc_session(agent_name="interview-coach")
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(vad=silero.VAD.load())
    order = tuple(random.sample(ARCHITECTURES, 2))

    async def wrap_up():
        await asyncio.sleep(3)
        session.shutdown()

    await ctx.connect()
    desk = InterviewDesk(session, ctx.room, order, on_done=wrap_up)

    async def close_desk():
        desk.close()

    ctx.add_shutdown_callback(close_desk)
    await session.start(agent=desk.first_agent(), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
