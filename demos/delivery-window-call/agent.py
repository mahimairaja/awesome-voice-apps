"""Delivery window call: an outbound voice agent that confirms a delivery.

The agent places the call, lets answering machine detection (AMD) decide who
picked up, and then either talks to the customer (press 1 to confirm, 2 to
reschedule, or just say it) or leaves a voicemail and hangs up.

The callee joins from the browser: the first participant who publishes a
microphone is the callee, publishing the mic is "picking up", and keypad
presses arrive with localParticipant.publishDtmf(). No phone line is needed.

Run it:
1. Copy .env.example to .env and fill the keys.
2. uv sync && uv run python agent.py download-files
3. uv run python agent.py dev, then dispatch:
   lk dispatch create --new-room --agent-name delivery-window-call
"""

import asyncio
import datetime
import json
import logging
import time

from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    AMD,
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    cli,
    function_tool,
    get_job_context,
    llm,
)
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger("delivery-window-call")

BRAND = "Northbound Home"
ITEM = "a three-seat sofa"
# Seconds to wait for the callee to publish a microphone before giving up.
RING_SECONDS = 25
DIGITS = frozenset("0123456789*#")

INSTRUCTIONS = (
    f"You are calling on behalf of {BRAND}, a furniture retailer, to confirm a "
    f"delivery of {ITEM}. You placed this call, so when the customer answers, "
    "say who you are and why you are calling in one sentence, read the current "
    "window, and tell them they can press 1 to confirm or 2 to reschedule, or "
    "just say so. To confirm, call confirm_window. To move it, read the other "
    "windows and call reschedule_window with the id they pick. After either, say "
    "a one-line goodbye and call end_call. If they ask something you cannot "
    "help with, say a person from the delivery team will follow up, then end. "
    "Keep every reply to one or two short sentences, plain text, no lists."
)


def build_windows(today: datetime.date | None = None) -> list[dict]:
    """The booked window plus three alternatives on upcoming weekdays."""
    day = today or datetime.date.today()
    days: list[datetime.date] = []
    while len(days) < 3:
        day += datetime.timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    label = lambda d, hours: f"{d:%A} {d:%B} {d.day}, {hours}"  # noqa: E731
    return [
        {"id": "w1", "label": label(days[1], "1 to 5 PM")},
        {"id": "w2", "label": label(days[0], "8 AM to noon")},
        {"id": "w3", "label": label(days[2], "8 AM to noon")},
        {"id": "w4", "label": label(days[2], "1 to 5 PM")},
    ]


def initial_state(today: datetime.date | None = None) -> dict:
    windows = build_windows(today)
    return {
        "stage": "idle",
        "started": None,
        "timeline": [],
        "amd": None,
        "keys": [],
        "menu": None,
        "order": {"ref": "NB-48213", "item": ITEM, "window": windows[0]["id"]},
        "windows": windows,
        "outcome": None,
        "mounted": False,
    }


def snapshot(data: dict) -> dict:
    """The display state sent to the page: no audio, nothing private."""
    return {
        key: data[key] for key in ("stage", "timeline", "amd", "keys", "menu", "order", "windows")
    } | {"outcome": data["outcome"]}


def publish(room: rtc.Room, data: dict) -> None:
    action = "update" if data["mounted"] else "mount"
    data["mounted"] = True
    payload = {
        "type": "ui_event",
        "component": "Delivery",
        "id": "delivery",
        "action": action,
        "props": snapshot(data),
    }

    async def send() -> None:
        try:
            await room.local_participant.publish_data(
                json.dumps(payload).encode(), topic="ui", reliable=True
            )
        except Exception:
            logger.exception("failed to publish delivery state")

    asyncio.ensure_future(send())


def window(data: dict, window_id: str) -> dict | None:
    return next((w for w in data["windows"] if w["id"] == window_id), None)


def alternatives(data: dict) -> list[dict]:
    return [w for w in data["windows"] if w["id"] != data["order"]["window"]][:3]


def voicemail_text(data: dict) -> str:
    current = window(data, data["order"]["window"])
    return (
        f"Hi, this is {BRAND} calling about your delivery of {ITEM}, order "
        f"{' '.join(data['order']['ref'])}. It is booked for {current['label']}. "
        "To confirm or change it, reply to the text we sent or call us back. Thank you."
    )


class DeliveryCaller(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self.callee: str | None = None
        self._hung_up = False
        self._call: asyncio.Task | None = None

    # region: call state

    def mark(self, stage: str) -> None:
        data = self.session.userdata
        if data["started"] is None:
            data["started"] = time.monotonic()
        data["stage"] = stage
        data["timeline"].append(
            {"stage": stage, "ms": int((time.monotonic() - data["started"]) * 1000)}
        )
        publish(self.room, data)

    def elapsed(self) -> int:
        started = self.session.userdata["started"] or time.monotonic()
        return int((time.monotonic() - started) * 1000)

    # endregion

    async def on_enter(self) -> None:
        self.session.userdata = initial_state()
        self._call = asyncio.ensure_future(self.run_call())

    async def run_call(self) -> None:
        """Dial, wait for an answer, classify it, then talk or leave a message."""
        session = self.session
        self.room.on("sip_dtmf_received", self._on_dtmf)
        self.mark("dialling")
        try:
            self.mark("ringing")
            if not await self._wait_for_answer():
                return
            self.room.on("participant_disconnected", self._on_left)
            self.mark("answered")
            async with AMD(
                session,
                # Reuse this agent's LLM and the session's transcripts so the
                # classifier runs on the providers the call already pays for.
                llm=self.llm if isinstance(self.llm, llm.LLM) else session.llm,
                stt=None,
                participant_identity=self.callee,
                ivr_detection=False,
                suppress_compatibility_warning=True,
                detection_options={"no_speech_threshold": 6.0},
            ) as detector:
                result = await detector.execute()
            await self._on_amd(result)
        except Exception:
            logger.exception("delivery call failed")
            await self.hang_up()

    async def _wait_for_answer(self) -> bool:
        """Publishing the microphone is picking up the phone."""
        answered = asyncio.Event()

        def check(*_args) -> None:
            for participant in self.room.remote_participants.values():
                if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
                    continue
                if any(
                    pub.kind == rtc.TrackKind.KIND_AUDIO
                    for pub in participant.track_publications.values()
                ):
                    self.callee = participant.identity
                    answered.set()

        self.room.on("track_published", check)
        check()
        try:
            await asyncio.wait_for(answered.wait(), RING_SECONDS)
        except asyncio.TimeoutError:
            self.mark("no-answer")
            await self._end("no-answer")
            return False
        finally:
            self.room.off("track_published", check)
        return True

    async def _on_amd(self, result) -> None:
        data = self.session.userdata
        category = str(getattr(result.category, "value", result.category))
        data["amd"] = {
            "category": category,
            "reason": str(result.reason)[:60],
            "heard": result.transcript.strip()[:160],
            "speech_ms": int(result.speech_duration * 1000),
            "delay_ms": int(result.delay * 1000),
        }
        if category == "machine-vm":
            self.mark("voicemail")
            handle = self.session.say(voicemail_text(data), allow_interruptions=False)
            await handle.wait_for_playout()
            await self._end("voicemail-left")
            return
        if category in {"machine-unavailable", "machine-ivr"}:
            self.mark("unavailable")
            await self._end("unavailable")
            return
        data["menu"] = "main"
        self.mark("human")
        if category == "uncertain":
            # Picked up and said nothing: open the conversation ourselves.
            self.session.generate_reply(
                instructions="The customer picked up but is silent. Introduce the call."
            )

    # region: keypad

    def _on_dtmf(self, dtmf: rtc.SipDTMF) -> None:
        sender = dtmf.participant.identity if dtmf.participant else None
        if sender != self.callee or dtmf.digit not in DIGITS:
            return
        asyncio.ensure_future(self.press(dtmf.digit))

    async def press(self, digit: str) -> None:
        """Keypad input takes a fixed path: no LLM round trip, no ambiguity."""
        data = self.session.userdata
        if data["stage"] != "human" or data["outcome"]:
            return
        menu = data["menu"]
        meaning = "not an option"
        reply = None
        if menu == "main" and digit == "1":
            meaning = "confirm"
            reply = self.confirm()
        elif menu == "main" and digit == "2":
            meaning = "reschedule"
            data["menu"] = "reschedule"
            options = alternatives(data)
            reply = (
                " ".join(f"Press {i + 1} for {w['label']}." for i, w in enumerate(options))
                + " Press 0 to keep the current window."
            )
        elif menu == "reschedule" and digit in {"1", "2", "3"}:
            picked = alternatives(data)[int(digit) - 1]
            meaning = f"pick {picked['label']}"
            reply = self.reschedule(picked["id"])
        elif menu == "reschedule" and digit == "0":
            meaning = "back"
            data["menu"] = "main"
            reply = "No change. Press 1 to confirm, or 2 to reschedule."
        else:
            reply = (
                "Press 1 to confirm, or 2 to reschedule."
                if menu == "main"
                else "Press 1, 2 or 3 to pick a window, or 0 to go back."
            )
        data["keys"] = (
            data["keys"] + [{"digit": digit, "meaning": meaning, "ms": self.elapsed()}]
        )[-12:]
        publish(self.room, data)
        # A key press barges in, exactly like speech does.
        self.session.interrupt()
        handle = self.session.say(reply)
        if data["outcome"]:
            await handle.wait_for_playout()
            await self.hang_up()

    # endregion

    # region: shared actions (keypad and voice land here)

    def confirm(self) -> str:
        data = self.session.userdata
        current = window(data, data["order"]["window"])
        data["outcome"] = "confirmed"
        data["menu"] = None
        self.mark("confirmed")
        return f"Confirmed for {current['label']}. Thanks, goodbye."

    def reschedule(self, window_id: str) -> str:
        data = self.session.userdata
        target = window(data, window_id)
        data["order"]["window"] = window_id
        data["outcome"] = "rescheduled"
        data["menu"] = None
        self.mark("rescheduled")
        return f"Done, moved to {target['label']}. Thanks, goodbye."

    @function_tool()
    async def confirm_window(self, context: RunContext[dict]) -> str:
        """Confirm the currently booked delivery window. Call when the customer agrees to it."""
        if context.userdata["outcome"]:
            return "Already settled. Say goodbye and call end_call."
        self.confirm()
        current = window(context.userdata, context.userdata["order"]["window"])
        return f"Confirmed for {current['label']}. Say a short goodbye and call end_call."

    @function_tool()
    async def reschedule_window(self, context: RunContext[dict], window_id: str) -> str:
        """Move the delivery to another window.

        Args:
            window_id: One of the alternative window ids, for example "w2".
        """
        data = context.userdata
        if data["outcome"]:
            return "Already settled. Say goodbye and call end_call."
        options = {w["id"]: w for w in alternatives(data)}
        if window_id not in options:
            listed = "; ".join(f"{w['id']}: {w['label']}" for w in options.values())
            return f"Unknown window. Offer these: {listed}"
        self.reschedule(window_id)
        return f"Moved to {options[window_id]['label']}. Say a short goodbye and call end_call."

    @function_tool()
    async def list_windows(self, context: RunContext[dict]) -> str:
        """List the alternative delivery windows the customer can move to."""
        return "; ".join(f"{w['id']}: {w['label']}" for w in alternatives(context.userdata))

    @function_tool()
    async def end_call(self, context: RunContext[dict]) -> None:
        """Hang up after the goodbye has been said."""
        await context.wait_for_playout()
        if not context.userdata["outcome"]:
            context.userdata["outcome"] = "follow-up"
            self.mark("follow-up")
        await self.hang_up()

    # endregion

    def _on_left(self, participant: rtc.RemoteParticipant) -> None:
        if participant.identity == self.callee and not self._hung_up:
            asyncio.ensure_future(self._end(self.session.userdata["outcome"] or "hung-up"))

    async def _end(self, outcome: str) -> None:
        data = self.session.userdata
        data["outcome"] = data["outcome"] or outcome
        data["menu"] = None
        self.mark("ended")
        await self.hang_up()

    async def hang_up(self) -> None:
        """End the call for everyone by deleting the room."""
        if self._hung_up:
            return
        self._hung_up = True
        ctx = get_job_context()
        try:
            await ctx.api.room.delete_room(api.DeleteRoomRequest(room=self.room.name))
        finally:
            ctx.shutdown("delivery call ended")


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="delivery-window-call")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    session = AgentSession(
        userdata={},
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4.1-mini"),
        tts=cartesia.TTS(model="sonic-3"),
        vad=ctx.proc.userdata["vad"],
    )
    await ctx.connect()
    await session.start(agent=DeliveryCaller(ctx.room), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
