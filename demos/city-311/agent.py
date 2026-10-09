"""Bilingual 311 city line voice agent.

A resident of the fictional City of Bellerive reports a pothole or asks about
garbage day, in English or French, and switches language mid-call. The agent
follows in the same language with a matching voice.

How it works:
- Deepgram Nova-3 in `multi` mode transcribes English and French in one stream
  and tags each final transcript with its dominant language.
- A small router decides when that tag is strong enough to switch the language
  of service. A one-word "oui" or "okay" does not flip the call; three words, or
  two short turns in a row, do. The caller can also just ask.
- One prompt serves both languages. Each turn gets a single line naming the
  language of service, and the TTS voice and language follow the same decision.
- Service requests use language-neutral category codes, so a ticket opened in
  French and one opened in English land in the same city queue, tagged with the
  language for the follow-up.

Run it:
1. Copy .env.example to .env and fill the keys.
2. Run: uv sync
3. Run: uv run python agent.py download-files
4. Run: uv run python agent.py console
"""

import asyncio
import json
import logging
import os
import random
from typing import Literal

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
from livekit.plugins.turn_detector.multilingual import MultilingualModel

load_dotenv()

logger = logging.getLogger(__name__)

# The languages of service. Voices are Cartesia voice ids; override them per
# deployment without touching the code.
LANGUAGES = {
    "en": {
        "name": "English",
        "reply": "English",
        "voice": os.environ.get("CITY311_VOICE_EN", "f786b574-daa5-4673-aa0c-cbe3e8534c02"),
        "voice_label": "English voice",
    },
    "fr": {
        "name": "Français",
        "reply": "Canadian French (use Quebec vocabulary: courriel, fin de semaine)",
        "voice": os.environ.get("CITY311_VOICE_FR", "a249eaff-1e96-4d2c-b23b-12efa4f66f41"),
        "voice_label": "French voice",
    },
}

# A switch needs this much evidence. Below it, the call stays in its language.
MIN_CONFIDENCE = 0.6
MIN_WORDS = 3
MIN_STREAK = 2

# Category codes stay in English so every ticket lands in one queue; the labels
# are what the panel and the agent read out.
CATEGORIES = {
    "pothole": ("Pothole", "Nid-de-poule"),
    "missed_collection": ("Missed collection", "Collecte manquée"),
    "streetlight": ("Streetlight out", "Lampadaire éteint"),
    "graffiti": ("Graffiti", "Graffiti"),
    "noise": ("Noise complaint", "Plainte pour bruit"),
    "fallen_tree": ("Fallen tree", "Arbre tombé"),
}

# Collection days by district. Fictional city, fictional schedule.
DISTRICTS = {
    "vieux-bellerive": ("Vieux-Bellerive", "Monday", "Thursday"),
    "les-saules": ("Les Saules", "Tuesday", "Friday"),
    "northgate": ("Northgate", "Wednesday", "Monday"),
    "bord-de-leau": ("Bord-de-l'Eau", "Thursday", "Tuesday"),
}
DAYS_FR = {
    "Monday": "lundi",
    "Tuesday": "mardi",
    "Wednesday": "mercredi",
    "Thursday": "jeudi",
    "Friday": "vendredi",
}

INSTRUCTIONS = (
    "You are the 311 line for the City of Bellerive, a bilingual Canadian city. "
    "This is a simulation: no real ticket is filed and nothing is saved. "
    "Residents call to report problems (potholes, missed garbage or recycling "
    "pickup, streetlights, graffiti, noise, fallen trees) and to ask which day "
    "garbage and recycling are collected. "
    "Serve every caller in the language of service named in the latest system "
    "line, even if their last sentence mixed languages, and never translate what "
    "they said back to them. The same rules, tools and facts apply in English and "
    "French. "
    "To report a problem, get the category and a location (a street and a cross "
    "street or a civic number), then call report_issue once. Read back the ticket "
    "number digit by digit. "
    "For collection days, ask for their district if they did not say it, then call "
    "collection_schedule. The districts are Vieux-Bellerive, Les Saules, Northgate "
    "and Bord-de-l'Eau. "
    "If the caller asks to be served in the other language, call set_language. "
    "Only English and French are offered; say so politely in the current language "
    "if they ask for another. "
    "Keep replies to one or two short sentences, plain text, no markdown or emojis."
)


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
) -> None:
    envelope = {"type": "ui_event", "component": component, "action": action, "props": props or {}}
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(
                json.dumps(envelope).encode("utf-8"), topic="ui", reliable=True
            )
        )
    except RuntimeError:
        logger.exception("failed to schedule ui event")
        return

    def log_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception():
            logger.warning("failed to publish ui event")

    task.add_done_callback(log_failure)


def base_language(code: object) -> str:
    """'fr-CA', 'fr' or a LanguageCode -> 'fr'."""
    text = str(getattr(code, "language", None) or code or "")
    return text.replace("_", "-").split("-")[0].lower()


class LanguageRouter:
    """Decides the language of service from per-utterance detections.

    Detection alone flips too easily: a French caller says "okay", an English
    caller says "merci". The router keeps the current language until the new one
    has enough words, or shows up twice in a row, at usable confidence.
    """

    def __init__(self) -> None:
        self.current: str | None = None
        self._candidate: str | None = None
        self._streak = 0

    def hear(self, detected: object, text: str, confidence: float) -> dict:
        lang = base_language(detected)
        words = len(text.split())
        decision = {
            "lang": lang or "?",
            "words": words,
            "confidence": round(max(0.0, min(1.0, float(confidence or 0))), 2),
            "outcome": "same",
            "reason": "",
        }
        if lang not in LANGUAGES:
            decision.update(outcome="held", reason="not offered")
        elif self.current is None:
            self.current = lang
            decision.update(outcome="start", reason="first words")
        elif lang == self.current:
            self._candidate, self._streak = None, 0
        else:
            self._streak = self._streak + 1 if self._candidate == lang else 1
            self._candidate = lang
            if decision["confidence"] < MIN_CONFIDENCE:
                decision.update(outcome="held", reason="low confidence")
            elif words >= MIN_WORDS or self._streak >= MIN_STREAK:
                self._switch(lang)
                decision.update(
                    outcome="switch",
                    reason=f"{words} words" if words >= MIN_WORDS else "twice in a row",
                )
            else:
                decision.update(outcome="held", reason="too short")
        decision["speaking"] = self.current or "en"
        return decision

    def ask(self, lang: str) -> dict:
        """The caller asked for a language outright: that always wins."""
        lang = base_language(lang)
        if lang not in LANGUAGES:
            raise ValueError("Only English and French are offered")
        self._switch(lang)
        return {
            "lang": lang,
            "words": 0,
            "confidence": 1.0,
            "outcome": "switch",
            "reason": "caller asked",
            "speaking": lang,
        }

    def _switch(self, lang: str) -> None:
        self.current = lang
        self._candidate, self._streak = None, 0

    @property
    def speaking(self) -> str:
        return self.current or "en"


def initial_state() -> dict:
    return {"router": LanguageRouter(), "tickets": []}


def make_stt() -> deepgram.STT:
    # `multi` is Nova-3's code-switching mode: one stream, a language per result.
    # Deepgram recommends 100 ms endpointing for code-switching (default is 25).
    return deepgram.STT(model="nova-3", language="multi", endpointing_ms=100)


def make_tts(lang: str = "en") -> cartesia.TTS:
    return cartesia.TTS(model="sonic-2", voice=LANGUAGES[lang]["voice"], language=lang)


def language_line(lang: str) -> str:
    return f"Language of service: {LANGUAGES[lang]['reply']}. Reply only in that language."


def publish_turn(room: rtc.Room, decision: dict, text: str) -> None:
    speaking = decision["speaking"]
    publish_ui_event(
        room,
        "LanguageTurn",
        "update",
        {
            **decision,
            "voice": LANGUAGES[speaking]["voice_label"],
            "excerpt": " ".join(text.split())[:80],
        },
    )


def publish_ticket(room: rtc.Room, ticket: dict) -> None:
    publish_ui_event(room, "ServiceRequest", "update", ticket)


def publish_schedule(room: rtc.Room, schedule: dict) -> None:
    publish_ui_event(room, "Collection", "update", schedule)


def publish_city311(room: rtc.Room, data: dict) -> None:
    publish_ui_event(
        room,
        "LanguageDesk",
        "mount",
        {"speaking": data["router"].speaking, "languages": sorted(LANGUAGES)},
    )


class City311Agent(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=INSTRUCTIONS,
            stt=make_stt(),
            llm=openai.LLM(model="gpt-4o-mini"),
            tts=make_tts(),
        )
        self.room = room
        self._voice = "en"

    @property
    def _router(self) -> LanguageRouter:
        return self.session.userdata["router"]

    async def stt_node(self, audio, model_settings):
        # Read the language tag on each final transcript before the turn ends,
        # so the decision is in place when the reply is generated.
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                heard = event.alternatives[0]
                if heard.text.strip():
                    self.hear(heard.language, heard.text, heard.confidence)
            yield event

    def hear(self, language: object, text: str, confidence: float) -> dict:
        decision = self._router.hear(language, text, confidence)
        publish_turn(self.room, decision, text)
        return decision

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        # One prompt for both languages; only this line changes per turn.
        turn_ctx.add_message(role="system", content=language_line(self._router.speaking))

    async def tts_node(self, text, model_settings):
        self._sync_voice()
        async for frame in Agent.default.tts_node(self, text, model_settings):
            yield frame

    def _sync_voice(self) -> None:
        lang = self._router.speaking
        if lang != self._voice and self.tts is not None:
            self.tts.update_options(voice=LANGUAGES[lang]["voice"], language=lang)
            self._voice = lang

    @function_tool()
    async def set_language(self, context: RunContext[dict], language: Literal["en", "fr"]) -> str:
        """Switch the language of service when the caller asks for English or French.

        Args:
            language: "en" for English, "fr" for French.
        """
        decision = context.userdata["router"].ask(language)
        publish_turn(self.room, decision, "")
        return f"Language set. {language_line(language)}"

    @function_tool()
    async def report_issue(
        self,
        context: RunContext[dict],
        category: Literal[
            "pothole", "missed_collection", "streetlight", "graffiti", "noise", "fallen_tree"
        ],
        location: str,
        details: str = "",
    ) -> str:
        """File a simulated 311 service request.

        Args:
            category: The problem type code, whatever language the caller used
                (nid-de-poule is pothole, collecte manquée is missed_collection).
            location: Street and cross street, or a civic number, as the caller said it.
            details: A few words on size, hazard or timing, if given.
        """
        location = " ".join(location.split())[:80]
        if len(location) < 3:
            return "Ask for a street and cross street, or a civic number, first."
        tickets = context.userdata["tickets"]
        if len(tickets) >= 3:
            return "This demo files up to three requests per call."
        lang = context.userdata["router"].speaking
        ticket = {
            "ref": f"311-{random.randint(100000, 999999)}",
            "category": category,
            "label": CATEGORIES[category][1 if lang == "fr" else 0],
            "location": location,
            "details": " ".join(details.split())[:120],
            "language": lang,
        }
        tickets.append(ticket)
        publish_ticket(self.room, ticket)
        digits = " ".join(ticket["ref"].replace("-", ""))
        return (
            f"Filed {ticket['ref']} ({CATEGORIES[category][0]}) at {location}. "
            f"Read the number as: {digits}. Follow-ups go out in {LANGUAGES[lang]['name']}."
        )

    @function_tool()
    async def collection_schedule(
        self,
        context: RunContext[dict],
        district: Literal["vieux-bellerive", "les-saules", "northgate", "bord-de-leau"],
    ) -> str:
        """Look up garbage and recycling days for a Bellerive district.

        Args:
            district: The district code.
        """
        name, garbage, recycling = DISTRICTS[district]
        lang = context.userdata["router"].speaking
        if lang == "fr":
            garbage, recycling = DAYS_FR[garbage], DAYS_FR[recycling]
        publish_schedule(
            self.room,
            {"district": name, "garbage": garbage, "recycling": recycling, "language": lang},
        )
        return f"{name}: garbage on {garbage}, recycling on {recycling}. Put bins out by 7 AM."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="city-311")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    session = AgentSession(
        userdata=initial_state(),
        vad=ctx.proc.userdata["vad"],
        turn_detection=MultilingualModel(),
    )
    await session.start(agent=City311Agent(ctx.room), room=ctx.room)
    await ctx.connect()
    publish_city311(ctx.room, session.userdata)
    await session.generate_reply(
        instructions=(
            "Open with 'Bellerive 311, bonjour, hi.' Then say in English that this is "
            "a simulation and they can report a problem or ask about garbage day, in "
            "English or French."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
