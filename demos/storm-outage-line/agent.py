"""Storm outage line: a utility's outage agent on a simulated phone network.

The caller reports a power outage by reading the service card on their screen.
Then they move their own call onto a landline or a bad cell connection, with
the telephony fixes off or on, and read the card again. Every reading is scored
against the card, so the effect of the phone network on speech-to-text is a
number, not an anecdote. The agent can play back how the line sounded.

Run it:
1. Copy .env.example to .env and fill the keys.
2. uv sync
3. uv run python agent.py download-files
4. uv run python agent.py console   (or `dev` to connect a LiveKit client)
"""

import asyncio
import json
import logging
import random
from typing import Literal

import numpy as np
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
from phone_line import LINE_LABELS, LineAudioInput, LineStats, PhoneLine, frames
from scoring import card_address, digits, expected_tokens, matched, new_card, tokens

load_dotenv()

logger = logging.getLogger(__name__)

UTILITY = "Northbay Power"
MAX_READINGS = 6
MAX_HEARD = 200

# Endpointing for clean audio, and for a line with gaps in it: a dropped packet
# looks like a pause, so on a bad line the agent waits longer before it decides
# the caller has finished.
WEB_ENDPOINTING = 0.5
PHONE_ENDPOINTING = 0.9

INSTRUCTIONS = f"""You answer the storm line for {UTILITY}, a fictional power utility,
in a two-minute public demo. Nothing is really reported.

The caller has a service card on their screen with a street address and an
eight-digit account number. You do not know what it says. You only know what you hear.

1. Ask them to read the address and account number. Call report_outage with
   exactly what you heard, even if it sounds wrong. If it finds no match, ask them
   to read the account number again, once, then call report_outage again.
2. Once there is a ticket, give the ticket number and the restoration estimate in
   one sentence, then tell them they can move this call onto a landline or a bad
   cell connection, and turn the telephony fixes on or off, by asking.
3. When they ask for a different line or for the fixes, call set_line, then ask
   them to read the card again. After that reading, repeat back the address and
   account number exactly as you heard them, digits one by one, and offer to play
   back how their voice reached you. If they want it, call play_back.

Never invent a score; the screen shows it. Keep every reply to one or two short
sentences, plain text, no lists. Say numbers digit by digit."""

GREETING = (
    f"Say: {UTILITY} storm line, this is a simulation and nothing is reported. "
    "Then ask them to read the service address and account number on their screen."
)


def publish_ui_event(room: rtc.Room, component: str, props: dict) -> None:
    payload = json.dumps(
        {"type": "ui_event", "component": component, "action": "update", "props": props}
    ).encode()

    async def send() -> None:
        try:
            await room.local_participant.publish_data(payload, topic="ui", reliable=True)
        except Exception:
            logger.exception("failed to publish ui event")

    try:
        asyncio.get_running_loop().create_task(send())
    except RuntimeError:
        logger.warning("no event loop for ui event")


def outage_stt() -> deepgram.STT:
    """Nova-3 with formatted numbers; keyterms are added when the fixes go on."""
    return deepgram.STT(model="nova-3", smart_format=True)


def initial_state(rng: random.Random | None = None) -> dict:
    return {"card": new_card(rng), "readings": [], "ticket": None}


def panel(data: dict, line: PhoneLine) -> dict:
    """Everything the screen shows, in one event."""
    return {
        "line": line.line,
        "fixes": line.fixes,
        "card": {"address": card_address(data["card"]), "account": data["card"]["account"]},
        "ticket": data["ticket"],
        "readings": [
            {key: value for key, value in reading.items() if not key.startswith("_")}
            for reading in data["readings"]
        ],
    }


class OutageLine(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self.line = PhoneLine()
        self._confidences: list[float] = []
        self._turn_start: int | None = None

    # -- the phone line --

    def install_line(self) -> None:
        """Route the caller's audio through the phone line before VAD and STT."""
        source = self.session.input.audio
        if source is None or isinstance(source, LineAudioInput):
            return
        self.session.input.audio = LineAudioInput(self.line, source)
        self.session.on("user_state_changed", self._on_user_state)
        self._open_reading()

    def _on_user_state(self, event) -> None:
        if event.new_state == "speaking":
            self._turn_start = self.line.samples_out

    def publish(self) -> None:
        publish_ui_event(self.room, "OutageLine", panel(self.session.userdata, self.line))

    def _open_reading(self) -> None:
        readings = self.session.userdata["readings"]
        if len(readings) >= MAX_READINGS:
            readings.pop(0)
        readings.append(
            {
                "line": self.line.line,
                "fixes": self.line.fixes,
                "heard": "",
                "matched": 0,
                "total": len(expected_tokens(self.session.userdata["card"])),
                "confidence": None,
                "turns": 0,
                "network": None,
                "_start": self.line.stats.snapshot(),
                "_confidences": [],
                "_spans": [],
            }
        )

    # -- scoring every reading against the card --

    async def stt_node(self, audio, model_settings):
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if (
                isinstance(event, stt.SpeechEvent)
                and event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
                and event.alternatives
                and event.alternatives[0].text
            ):
                self._confidences.append(float(event.alternatives[0].confidence))
            yield event

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        text = new_message.text_content or ""
        confidences, self._confidences = self._confidences, []
        span = (self._turn_start or self.line.samples_out, self.line.samples_out)
        self._turn_start = None
        data = self.session.userdata
        reading = data["readings"][-1]
        expected = expected_tokens(data["card"])
        heard = tokens(text)
        # The first turn after a line change is the reading. Later turns join it
        # only if they carry part of the card, so "switch to bad cell" does not.
        if reading["turns"] and not (matched(expected, heard) or digits(text)):
            return
        reading["turns"] += 1
        reading["heard"] = f"{reading['heard']} {text}".strip()[-MAX_HEARD:]
        reading["matched"] = matched(expected, tokens(reading["heard"]))
        reading["_confidences"].extend(confidences)
        if reading["_confidences"]:
            reading["confidence"] = round(float(np.mean(reading["_confidences"])), 2)
        reading["_spans"].append(span)
        if self.line.line != "web":
            stats: LineStats = self.line.stats.since(reading["_start"])
            reading["network"] = {
                "packets": stats.packets,
                "lost": stats.lost,
                "late": stats.late,
                "recovered": stats.recovered,
                "concealed": stats.concealed,
                "silent": stats.silent,
            }
        self.publish()

    # -- tools --

    @function_tool()
    async def report_outage(
        self, context: RunContext[dict], street_address: str, account_number: str
    ) -> str:
        """Log an outage for the caller's service address.

        Pass the street address and account number exactly as you heard them.
        """
        data = context.userdata
        card = data["card"]
        if data["ticket"]:
            return f"Already logged as {data['ticket']['ref']}."
        if digits(account_number) != digits(card["account"]):
            return (
                "No account matches that number. Ask them to read the account number again, "
                "digit by digit."
            )
        rng = random.Random()
        data["ticket"] = {
            "ref": f"OUT-{rng.randrange(10000, 99999)}",
            "address": card_address(card),
            "eta": f"{rng.randrange(2, 5)} hours",
            "nearby": rng.randrange(180, 900),
        }
        self.publish()
        ticket = data["ticket"]
        return (
            f"Logged {ticket['ref']} for {ticket['address']}. {ticket['nearby']} customers are "
            f"out on the same circuit; crews estimate power back in about {ticket['eta']}."
        )

    @function_tool()
    async def set_line(
        self,
        context: RunContext[dict],
        line: Literal["web", "landline", "cell"],
        fixes: bool,
    ) -> str:
        """Move this call onto another simulated line.

        line: "web" for clean browser audio, "landline" for an 8 kHz phone line,
        "cell" for a bad cell connection that drops and delays audio.
        fixes: true to turn on the telephony fixes, false to turn them off.
        Keep the current value of whichever one the caller did not mention.
        """
        self.line.set_line(line, fixes)
        card = context.userdata["card"]
        # The receive-side fixes live in PhoneLine. These are the STT-side ones:
        # prime the recognizer with the street on file (as a caller-ID lookup
        # would) and give a gappy line longer to finish a sentence.
        recognizer = self.stt or context.session.stt
        if isinstance(recognizer, deepgram.STT):
            recognizer.update_options(keyterm=[card["street"], UTILITY] if fixes else [])
        delay = PHONE_ENDPOINTING if fixes and line != "web" else WEB_ENDPOINTING
        context.session.update_options(endpointing_opts={"min_delay": delay})
        self._open_reading()
        self.publish()
        return (
            f"The call is now on {LINE_LABELS[line]} with the telephony fixes "
            f"{'on' if fixes else 'off'}. Ask them to read the card again."
        )

    @function_tool()
    async def play_back(self, context: RunContext[dict]) -> str:
        """Play the caller's last reading back to them, as it reached you."""
        readings = [r for r in context.userdata["readings"] if r["_spans"]]
        if not readings:
            return "There is no reading to play back yet."
        reading = readings[-1]
        start, end = reading["_spans"][0][0], reading["_spans"][-1][1]
        audio = self.line.clip(start, end)
        if not len(audio):
            return "That reading is too old to play back. Ask them to read the card again."

        async def clip():
            for frame in frames(audio, self.line.rate):
                yield frame

        context.session.say("(playback of the caller's line)", audio=clip(), add_to_chat_ctx=False)
        return "Playing it now. After it ends, say one short sentence about what they heard."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


class StandaloneOutageLine(OutageLine):
    async def on_enter(self) -> None:
        self.install_line()
        self.publish()
        await self.session.generate_reply(instructions=GREETING)


@server.rtc_session(agent_name="storm-outage-line")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    session = AgentSession(
        userdata=initial_state(),
        stt=outage_stt(),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-2"),
        vad=ctx.proc.userdata["vad"],
        turn_handling={"turn_detection": "vad"},
    )
    await session.start(agent=StandaloneOutageLine(ctx.room), room=ctx.room)
    await ctx.connect()


if __name__ == "__main__":
    cli.run_app(server)
