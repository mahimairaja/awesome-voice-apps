"""pronunciation-coach: a speaking coach that hears which words you got wrong.

Parlo, a fictional language-learning app, shows a line in English or French.
The learner reads it aloud. Deepgram returns each word with a recognition
confidence; the coach scores every word of the line, drills only the one or two
weakest, has the learner re-read the line to prove the fix, and moves them up
or down a level from their first read.

Patterns:
- Word-level confidence: `PronunciationCoach.stt_node` keeps every final word
  and its confidence for the turn. `coach.align` scores the line from them.
- Targeted feedback loop: scoring happens in code, in `on_user_turn_completed`.
  The LLM gets one system line naming the weak words and coaches only those.
- Adaptive difficulty: `coach._close` moves the level from the first read.
- Reading-aware turns: a longer endpointing delay so a pause mid-sentence does
  not cut the read in half, and no preemptive reply that scoring would discard.

Stack: Deepgram Nova-3 STT, OpenAI gpt-4o-mini, Cartesia Sonic 3 TTS.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py download-files
4. uv run python agent.py console
"""

import asyncio
import json
import logging
from typing import Literal

import coach
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
    llm,
    stt,
)
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    f"You are the speaking coach in {coach.APP}, a language-learning app. This is a "
    "simulation: nothing is saved. First ask whether they want to practise English or "
    "French, then call choose_language. The line to read is on their screen. "
    "After each read you get a system line with the scores, computed from what the "
    "speech recogniser heard. Follow it exactly: coach only the words it names, never "
    "invent scores, and never correct words it says were clear. "
    "If they ask for another line, call skip_line. "
    "When practising French, speak simple French; when practising English, speak "
    "simple English. Keep every reply to one or two short sentences, plain text, no "
    "markdown, no emojis, no phonetic symbols."
)
GREETING = (
    f"Say you are the {coach.APP} speaking coach, this is a simulation, and ask in one "
    "short sentence whether they want to practise English or French."
)


def make_stt(language: str = "en-US") -> deepgram.STT:
    # No keyterms: biasing the recogniser toward the line would hide the errors.
    return deepgram.STT(model="nova-3", language=language)


def make_tts(language: str = "en") -> cartesia.TTS:
    # Sonic 3 voices are multilingual, so one voice serves both languages.
    return cartesia.TTS(model="sonic-3", language=language)


def publish_ui_event(room: rtc.Room, component: str, props: dict) -> None:
    envelope = {"type": "ui_event", "component": component, "action": "update", "props": props}
    payload = json.dumps(envelope).encode("utf-8")
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except RuntimeError:
        logger.exception("failed to schedule playground ui event")
        return
    task.add_done_callback(lambda t: t.cancelled() or t.exception())


def publish_coach(room: rtc.Room, state: dict) -> None:
    publish_ui_event(room, "Pronounce", coach.snapshot(state))


class PronunciationCoach(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=INSTRUCTIONS,
            stt=make_stt(),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=make_tts(),
            turn_handling={
                # Learners pause mid-line while reading; wait before ending the turn.
                "endpointing": {"min_delay": 0.9, "max_delay": 3.0},
                # The reply depends on the score, so a preemptive draft is always wasted.
                "preemptive_generation": {"enabled": False},
            },
        )
        self.room = room
        self._heard: list[tuple[str, float]] = []

    async def stt_node(self, audio, model_settings):
        # Keep every final word with its confidence until the turn ends. One read
        # can arrive as several final segments.
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                self.hear(event.alternatives[0])
            yield event

    def hear(self, alternative: stt.SpeechData) -> None:
        for word in alternative.words or []:
            self._heard.append((str(word), word.confidence))
        if not alternative.words and alternative.text.strip():
            # A recogniser without word timings: spread the utterance confidence.
            self._heard += [(w, alternative.confidence) for w in alternative.text.split()]

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        heard, self._heard = self._heard[-60:], []
        state = self.session.userdata
        result = coach.score_turn(state, heard)
        if result is None:
            return
        publish_coach(self.room, state)
        turn_ctx.add_message(role="system", content=coach.brief(state, result))

    def set_language(self, language: str) -> None:
        settings = coach.LANGUAGES[language]
        if self.stt is not None:
            self.stt.update_options(language=settings["stt"])
        if self.tts is not None:
            self.tts.update_options(language=settings["tts"])

    @function_tool()
    async def choose_language(
        self, context: RunContext[dict], language: Literal["en", "fr"]
    ) -> str:
        """Start practice in the language the learner picked.

        Args:
            language: "en" to practise English, "fr" to practise French.
        """
        phrase = coach.choose_language(context.userdata, language)
        self.set_language(language)
        self._heard = []
        publish_coach(self.room, context.userdata)
        name = coach.LANGUAGES[language]["name"]
        return (
            f"Practising {name} at {coach.LEVELS[context.userdata['level']]} level. From now "
            f"on speak {name}. Say the line slowly once, '{phrase['text']}', and ask them to "
            "read it aloud from the screen."
        )

    @function_tool()
    async def skip_line(self, context: RunContext[dict]) -> str:
        """Give the learner a different line at the same level."""
        if not context.userdata["language"]:
            return "Ask whether they want English or French first."
        phrase = coach.skip(context.userdata)
        publish_coach(self.room, context.userdata)
        return f"New line: '{phrase['text']}'. Say it slowly once and ask them to read it."


def initial_state() -> dict:
    return coach.initial_state()


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="pronunciation-coach")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    session = AgentSession(
        userdata=initial_state(),
        vad=ctx.proc.userdata["vad"],
        turn_handling={"turn_detection": "vad"},
    )
    await session.start(agent=PronunciationCoach(ctx.room), room=ctx.room)
    await ctx.connect()
    publish_coach(ctx.room, session.userdata)
    await session.generate_reply(instructions=GREETING)


if __name__ == "__main__":
    cli.run_app(server)
