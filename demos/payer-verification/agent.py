"""Payer verification call: a voice agent that calls an insurer for a clinic.

The agent dials a (simulated) payer, works through its phone tree by sending
keypad tones, waits on hold, notices when a person picks up, and turns that
person's answers into a structured eligibility and benefits record.

You play the payer's representative. The phone tree and hold music are played
by the agent's side of the call; the moment you speak like a person, the agent
takes over the conversation. Your "screen" of benefits to read out is on the
page (or in the logs when you run it locally).

Run it:
1. Copy .env.example to .env and fill the keys.
2. uv sync && uv run python agent.py download-files
3. uv run python agent.py console   (or `dev` and join from the Agents Playground)
"""

import asyncio
import json
import logging
import secrets
import time
from typing import Literal

import payer
from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    StopResponse,
    cli,
    function_tool,
    get_job_context,
    llm,
)
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger("payer-verification")

# Cartesia voices: the agent sounds like a person, the phone tree like a phone tree.
AGENT_VOICE = "f786b574-daa5-4673-aa0c-cbe3e8534c02"  # Katie
IVR_VOICE = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"  # Jacqueline
DECIDE_TIMEOUT = 4.0

INSTRUCTIONS = f"""You are {payer.AGENT_NAME}, an AI assistant calling {payer.PAYER} on behalf of \
{payer.PRACTICE} to verify a patient's eligibility and benefits. You already went through the \
phone menu and waited on hold. The person speaking now is a {payer.PAYER} provider services \
representative.

Case file. Share only what the rep asks for:
- Patient {payer.CASE["patient"]}, date of birth March 14, 1986.
- Member ID {payer.CASE["member_id"]}, read in groups. Group number {payer.CASE["group"]}.
- Provider NPI {" ".join(payer.NPI)}, read digit by digit.
- Service: {payer.CASE["service"]}, CPT {" ".join(payer.CASE["cpt"])}, plus specialist visits.

Open with one sentence: who you are, that you are an AI assistant, and that you are verifying \
eligibility and benefits for a patient.

You need coverage status, the specialist copay, the deductible and how much is met, \
coinsurance, whether CPT 70551 needs prior authorization, and a call reference number. Plan \
name, network status and the rep's name are nice to have.

Whenever the rep states any of these, call record_benefits with what they said before you \
reply, corrections included. Numbers go in as numbers. Follow its notes: ask about anything \
under Problems, then ask for what is Still needed, at most two items at a time. Never guess or \
fill in a value the rep did not say. When nothing required is missing, call \
finish_verification and do what it says.

Keep every reply to one or two short sentences, plain text, no lists."""

NAVIGATOR = f"""You are navigating {payer.PAYER}'s automated phone menu for {payer.PRACTICE}. \
Goal: reach a person who can verify a patient's eligibility and benefits. Provider NPI: \
{payer.NPI}. Call press_keys with exactly the keys this menu asks for (digits, * or #, in \
order) and a reason under ten words. If the menu asks for nothing, send empty keys."""

DETECTOR = f"""You monitor a call that is on hold with {payer.PAYER}. Decide whether this \
transcript is a live representative who just picked up, or a recording (hold announcements, \
ads, wait times, "please continue to hold"). People greet, give their name, ask for the \
caller's details or react to silence. Recordings are scripted and impersonal. Call \
report_speaker with your verdict and a reason under ten words."""


@function_tool
async def press_keys(keys: str, reason: str) -> None:
    """Press keys on the phone keypad.

    Args:
        keys: Keys to press in order, for example "2" or "1234567893#". Empty to wait.
        reason: Why these keys, in under ten words.
    """


@function_tool
async def report_speaker(speaker: Literal["human", "recording"], reason: str) -> None:
    """Report who is speaking on the line.

    Args:
        speaker: "human" for a live representative, "recording" for anything automated.
        reason: Why, in under ten words.
    """


def initial_state(seed: int | None = None) -> dict:
    seed = secrets.randbits(32) if seed is None else seed
    return {
        "seed": seed,
        "stage": "idle",
        "started": None,
        "timeline": [],
        "ivr": [],
        "hold": [],
        "heard": [],
        "record": payer.new_record(),
        "screen": payer.rep_screen(seed),
        "outcome": None,
        "mounted": False,
    }


def snapshot(data: dict) -> dict:
    """What the page draws: the call so far and the record. No audio, no transcript."""
    record = data["record"]
    return {
        "payer": payer.PAYER,
        "practice": payer.PRACTICE,
        "case": {
            "patient": payer.CASE["patient"],
            "member": payer.CASE["member_id"],
            "service": payer.CASE["service"],
            "cpt": payer.CASE["cpt"],
        },
        "stage": data["stage"],
        "timeline": data["timeline"][-10:],
        "ivr": data["ivr"][-6:],
        "hold": data["hold"][-4:],
        "fields": [
            {
                "field": name,
                "label": label,
                "value": record[name]["display"],
                "status": record[name]["status"],
                "source": record[name]["source"],
                "prev": record[name]["prev"],
                "required": name in payer.required(record),
            }
            for name, label in payer.FIELDS
        ],
        "missing": payer.missing(record),
        "remaining": payer.remaining(record),
        "screen": data["screen"],
        "result": payer.summary(record) if data["outcome"] == "verified" else None,
        "outcome": data["outcome"],
    }


def publish(room: rtc.Room, data: dict) -> None:
    action = "update" if data["mounted"] else "mount"
    data["mounted"] = True
    payload = {
        "type": "ui_event",
        "component": "Payer",
        "id": "payer",
        "action": action,
        "props": snapshot(data),
    }

    async def send() -> None:
        try:
            await room.local_participant.publish_data(
                json.dumps(payload).encode(), topic="ui", reliable=True
            )
        except Exception:
            logger.exception("failed to publish payer state")

    asyncio.ensure_future(send())


def spell(text: str) -> str:
    """Read codes one character at a time so the rep can check them."""
    return " ".join(text)


class PayerCaller(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=INSTRUCTIONS,
            tts=cartesia.TTS(model="sonic-3", voice=AGENT_VOICE),
        )
        self.room = room
        self.line = payer.PayerLine()
        self.heard = ""
        self._hung_up = False
        self._call: asyncio.Task | None = None
        self._hold: asyncio.Task | None = None
        self._playing = None

    # region: call state

    def mark(self, stage: str) -> None:
        data = self.session.userdata
        if data["started"] is None:
            data["started"] = time.monotonic()
        data["stage"] = stage
        data["timeline"].append({"stage": stage, "ms": self.elapsed()})
        publish(self.room, data)

    def elapsed(self) -> int:
        started = self.session.userdata["started"] or time.monotonic()
        return int((time.monotonic() - started) * 1000)

    def allow_side_call(self) -> bool:
        """Hosting hook: count model calls made outside the reply pipeline."""
        return True

    # endregion

    # region: audio on the line

    def voice(self, voice_id: str) -> None:
        tts = self.tts or self.session.tts
        if hasattr(tts, "update_options"):
            tts.update_options(voice=voice_id)

    async def play(self, pcm, *, interruptible: bool = False, label: str = "(tones)"):
        async def audio():
            for chunk in payer.frames(pcm):
                yield rtc.AudioFrame(chunk.tobytes(), payer.SAMPLE_RATE, 1, payer.FRAME)

        self._playing = self.session.say(
            label, audio=audio(), allow_interruptions=interruptible, add_to_chat_ctx=False
        )
        await self._playing.wait_for_playout()

    async def line_says(self, text: str, *, interruptible: bool = False) -> None:
        """The payer's recorded voice. Never part of the agent's conversation."""
        self.voice(IVR_VOICE)
        self._playing = self.session.say(
            text, allow_interruptions=interruptible, add_to_chat_ctx=False
        )
        await self._playing.wait_for_playout()

    async def send_keys(self, keys: str) -> None:
        """Send each key as a DTMF event and play its tones, the way a phone does."""

        async def events() -> None:
            for key in keys:
                try:
                    # On a SIP call this reaches the payer as an RFC 4733 event.
                    await self.room.local_participant.publish_dtmf(
                        code=payer.DTMF_CODE[key], digit=key
                    )
                except Exception:
                    logger.debug("dtmf event not delivered", exc_info=True)
                await asyncio.sleep(0.16)

        if keys:
            sender = asyncio.ensure_future(events())
            await self.play(payer.dtmf_audio(keys))
            await sender

    # endregion

    # region: side calls to the model (menu choice and hold detection)

    async def ask(self, system: str, text: str, tool) -> dict | None:
        """One tool call from the agent's own LLM, so it is metered like any turn."""
        if not self.allow_side_call():
            return None
        model = self.llm if isinstance(self.llm, llm.LLM) else self.session.llm
        ctx = llm.ChatContext.empty()
        ctx.add_message(role="system", content=system)
        ctx.add_message(role="user", content=text)
        args = None
        # Read the stream to the end: usage is reported when it completes.
        async with model.chat(chat_ctx=ctx, tools=[tool], tool_choice="required") as stream:
            async for chunk in stream:
                for call in (chunk.delta and chunk.delta.tool_calls) or []:
                    if args is None:
                        args = json.loads(call.arguments or "{}")
        return args

    async def choose_keys(self, prompt: str) -> tuple[str, str, str]:
        """Pick keys for a menu the way it was heard. Returns (keys, reason, how)."""
        try:
            async with asyncio.timeout(DECIDE_TIMEOUT):
                args = await self.ask(NAVIGATOR, f'The menu says: "{prompt}"', press_keys)
            if args is not None:
                keys = payer.clean_keys(str(args.get("keys", "")))
                return keys, str(args.get("reason", ""))[:80], "model"
        except Exception:
            logger.warning("menu choice failed; using the fallback path", exc_info=True)
        # The deterministic path keeps the call moving if the model is slow or down.
        return self.line.expected(), "model unavailable, known route", "fallback"

    async def classify(self, text: str) -> tuple[str, str, dict]:
        data = self.session.userdata
        signals = payer.hold_signals(text, data["heard"])
        try:
            async with asyncio.timeout(DECIDE_TIMEOUT):
                args = await self.ask(DETECTOR, f'On the line: "{text}"', report_speaker)
            if args and args.get("speaker") in {"human", "recording"}:
                verdict = args["speaker"]
                # A transcript that repeats an earlier one word for word is a loop.
                if signals["repeated"]:
                    verdict = "recording"
                return verdict, str(args.get("reason", ""))[:80], signals
        except Exception:
            logger.warning("hold detection failed; using signals", exc_info=True)
        return payer.fallback_verdict(signals), "phrase signals only", signals

    # endregion

    async def on_enter(self) -> None:
        self.session.userdata = initial_state()
        logger.info("rep screen for this call: %s", self.session.userdata["screen"])
        self._call = asyncio.ensure_future(self.run_call())

    async def run_call(self) -> None:
        """Dial, work the phone tree, wait on hold. The rep picking up ends this."""
        data = self.session.userdata
        try:
            self.line = payer.PayerLine(data["seed"])
            self.mark("dialing")
            await self.play(payer.ringback_audio(1), label="(ringing)")
            self.mark("ivr")
            while not self.line.on_hold and not self._hung_up:
                prompt = self.line.prompt()
                # Decide while the menu plays, as a person would think while listening.
                decision = asyncio.ensure_future(self.choose_keys(prompt))
                await self.line_says(prompt)
                keys, reason, how = await decision
                await self.send_keys(keys)
                ok, reply = self.line.press(keys)
                data["ivr"].append(
                    {
                        "menu": prompt[:220],
                        "keys": keys,
                        "reason": reason,
                        "result": "fallback"
                        if how == "fallback" and ok
                        else "ok"
                        if ok
                        else "invalid",
                    }
                )
                publish(self.room, data)
                if reply:
                    await self.line_says(reply)
            await self.line_says(payer.HOLD_GREETING)
            if data["stage"] == "ivr":
                self.mark("hold")
                self._hold = asyncio.ensure_future(self.hold())
        except Exception:
            logger.exception("payer call failed")
            await self.hang_up()

    async def hold(self) -> None:
        """Hold music, with the payer's message every other loop, until a person speaks."""
        loops = 0
        while self.session.userdata["stage"] == "hold":
            await self.play(payer.hold_music(8.0), interruptible=True, label="(hold music)")
            loops += 1
            if loops % 2 == 0 and self.session.userdata["stage"] == "hold":
                await self.line_says(payer.HOLD_MESSAGE, interruptible=True)

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        data = self.session.userdata
        text = (new_message.text_content or "").strip()
        stage = data["stage"]
        if stage == "hold" and text:
            verdict, reason, signals = await self.classify(text)
            data["hold"].append(
                {
                    "heard": text[:120],
                    "verdict": verdict,
                    "reason": reason,
                    "ms": self.elapsed(),
                    **signals,
                }
            )
            data["heard"] = (data["heard"] + [text])[-6:]
            if verdict == "human":
                self.take_over()
                self.heard = text
                return
            publish(self.room, data)
            raise StopResponse()
        if stage in {"human", "verified"}:
            self.heard = text
            return
        # Dialling, the phone tree, or after the call: nobody to answer yet.
        raise StopResponse()

    def take_over(self) -> None:
        """A person picked up: stop the hold loop and speak as the agent."""
        data = self.session.userdata
        data["stage"] = "human"
        if self._hold:
            self._hold.cancel()
        if self._playing and not self._playing.done():
            self._playing.interrupt(force=True)
        self.voice(AGENT_VOICE)
        self.mark("human")

    # region: tools for the conversation with the rep

    @function_tool()
    async def record_benefits(
        self,
        context: RunContext[dict],
        coverage: Literal["active", "inactive"] | None = None,
        plan: str | None = None,
        network: Literal["in", "out"] | None = None,
        specialist_copay_usd: float | None = None,
        deductible_usd: float | None = None,
        deductible_met_usd: float | None = None,
        coinsurance_percent: float | None = None,
        prior_auth_required: bool | None = None,
        reference_number: str | None = None,
        rep_name: str | None = None,
    ) -> str:
        """Record eligibility and benefits the rep just stated. Pass only what they said.

        Args:
            coverage: Whether the patient's plan is active.
            plan: Plan name, for example "PPO Choice".
            network: Whether the practice is in or out of network.
            specialist_copay_usd: Specialist office visit copay in dollars.
            deductible_usd: Annual individual deductible in dollars.
            deductible_met_usd: How much of the deductible is met so far, in dollars.
            coinsurance_percent: Patient coinsurance as a percentage, for example 20.
            prior_auth_required: Whether CPT 70551 needs prior authorization.
            reference_number: The call reference number, exactly as spelled.
            rep_name: The representative's name.
        """
        given = locals()
        data = context.userdata
        updates = {field: given[arg] for arg, field in payer.ARGS.items()}
        result = payer.apply(data["record"], updates, self.heard)
        publish(self.room, data)
        return result

    @function_tool()
    async def finish_verification(self, context: RunContext[dict]) -> str:
        """Close out the verification once everything required is captured."""
        data = context.userdata
        gaps = payer.missing(data["record"])
        if gaps:
            return "Not done. Still needed: " + ", ".join(payer.LABELS[g] for g in gaps) + "."
        if data["outcome"] != "verified":
            data["outcome"] = "verified"
            self.mark("verified")
        reference = data["record"]["reference"]["value"]
        return (
            f"Verified. Read the reference back as: {spell(reference)}. "
            "Thank the rep in one sentence, then call end_call."
        )

    @function_tool()
    async def end_call(self, context: RunContext[dict]) -> None:
        """Hang up after the goodbye has been said."""
        await context.wait_for_playout()
        data = context.userdata
        if not data["outcome"]:
            data["outcome"] = "incomplete"
        self.mark("ended")
        await self.hang_up()

    # endregion

    async def hang_up(self) -> None:
        """End the call for everyone."""
        if self._hung_up:
            return
        self._hung_up = True
        ctx = get_job_context()
        try:
            await ctx.api.room.delete_room(api.DeleteRoomRequest(room=self.room.name))
        finally:
            ctx.shutdown("payer call ended")


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="payer-verification")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    session = AgentSession(
        userdata={},
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4.1-mini"),
        vad=ctx.proc.userdata["vad"],
    )
    await ctx.connect()
    agent = PayerCaller(ctx.room)
    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
