"""medical-bill-explainer: member services that reads the bill you share mid-call.

A member of Kestrel Health, a made-up health plan, does not understand a bill.
During the call they send a photo of it over a LiveKit byte stream. The agent
reads it with a vision model into structured lines, checks the arithmetic in
plain Python (duplicates, balance billing, totals), explains each line only
from what it read, highlighting that line on screen, and opens a dispute when
the member agrees.

Stack: Deepgram STT, OpenAI LLM and vision, Cartesia TTS.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py dev
4. uv run python send_bill.py <room> samples/urgent-care-eob.png
   (or send the file from any LiveKit client on the "bill-upload" topic).
"""

import asyncio
import base64
import io
import json
import logging
import time
import zlib
from decimal import Decimal, InvalidOperation

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
)
from livekit.plugins import cartesia, deepgram, openai, silero
from openai import AsyncOpenAI
from PIL import Image

load_dotenv()

logger = logging.getLogger(__name__)

UPLOAD_TOPIC = "bill-upload"
UPLOAD_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_UPLOAD_BYTES = 4_000_000
MAX_UPLOADS = 3
# Long enough for small print to stay legible, short enough to bound the image tokens.
MAX_SIDE = 1600
MAX_LINES = 12
MAX_DISPUTES = 2
VISION_MODEL = "gpt-4.1-mini"
CENT = Decimal("0.01")
# Two amounts closer than this are the same amount.
TOLERANCE = Decimal("0.01")

VISION_PROMPT = (
    "You read a US medical bill or explanation of benefits from a photo. Copy what is "
    "printed; never calculate, infer or fix a number. For each service line give the "
    "date of service, the procedure code, the service description, the amount billed "
    "(charges), the plan allowed amount, the amount the plan paid, the amount the "
    "patient owes (your balance, you owe, patient responsibility) and the remark text. "
    "Use 0 for an amount column the document does not have. kind is eob for an "
    "explanation of benefits, provider_bill for a statement from a provider, other "
    "for anything else. network is in when the document says the provider is "
    "in-network, out when it says out-of-network, unknown otherwise. total_you_owe is "
    "the printed total the patient owes. readable is false when the image is not a "
    "medical bill or the lines cannot be read."
)

AMOUNT = {"type": "number"}
TEXT = {"type": "string"}
LINE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "date",
        "code",
        "description",
        "billed",
        "allowed",
        "plan_paid",
        "you_owe",
        "remark",
    ],
    "properties": {
        "date": TEXT,
        "code": TEXT,
        "description": TEXT,
        "billed": AMOUNT,
        "allowed": AMOUNT,
        "plan_paid": AMOUNT,
        "you_owe": AMOUNT,
        "remark": TEXT,
    },
}
VISION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "medical_bill",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["readable", "kind", "provider", "network", "lines", "total_you_owe"],
            "properties": {
                "readable": {"type": "boolean"},
                "kind": {"type": "string", "enum": ["eob", "provider_bill", "other"]},
                "provider": TEXT,
                "network": {"type": "string", "enum": ["in", "out", "unknown"]},
                "lines": {"type": "array", "items": LINE_SCHEMA},
                "total_you_owe": AMOUNT,
            },
        },
    },
}

KINDS = {
    "eob": "Explanation of benefits",
    "provider_bill": "Provider statement",
    "other": "Document",
}
# What each check found, in words the agent can say. Code decides, the model explains.
FINDINGS = {
    "duplicate": "Billed twice: same code, same day, same amount as an earlier line.",
    "balance_billed": (
        "You are charged more than the plan's allowed amount minus what the plan paid. "
        "An in-network provider agreed to the allowed amount, so the difference should "
        "not be billed to you."
    ),
    "total_mismatch": "The printed total does not match the sum of the lines.",
}
TERMS = (
    "Billed is the provider's list price. Allowed is the price the plan negotiated. "
    "Plan paid is the plan's share of the allowed amount. You owe is what is left: "
    "for in-network care, at most allowed minus plan paid."
)


def money(value) -> Decimal:
    """A printed amount as cents; anything that is not a sane amount becomes zero."""
    try:
        amount = Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(0)
    if not amount.is_finite() or amount < 0 or amount > 1_000_000:
        return Decimal(0)
    return amount


def say_money(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def clip(value, size: int) -> str:
    return " ".join(str(value or "").split())[:size]


def normalize(reading: dict) -> dict:
    """Bound and type everything the vision model returned before anyone uses it."""
    lines = []
    for i, raw in enumerate((reading.get("lines") or [])[:MAX_LINES]):
        if not isinstance(raw, dict):
            continue
        lines.append(
            {
                "id": f"L{i + 1}",
                "date": clip(raw.get("date"), 20),
                "code": clip(raw.get("code"), 16),
                "description": clip(raw.get("description"), 60),
                "billed": money(raw.get("billed")),
                "allowed": money(raw.get("allowed")),
                "plan_paid": money(raw.get("plan_paid")),
                "you_owe": money(raw.get("you_owe")),
                "remark": clip(raw.get("remark"), 40),
            }
        )
    kind = reading.get("kind")
    network = reading.get("network")
    return {
        "readable": bool(reading.get("readable")) and bool(lines),
        "kind": kind if kind in KINDS else "other",
        "provider": clip(reading.get("provider"), 60),
        "network": network if network in ("in", "out", "unknown") else "unknown",
        "lines": lines,
        "total_you_owe": money(reading.get("total_you_owe")),
    }


def audit(bill: dict) -> list[dict]:
    """The checks a billing advocate does first. Plain arithmetic, no model."""
    findings = []
    seen = {}
    for line in bill["lines"]:
        key = (line["date"], line["code"].upper(), line["billed"])
        if line["code"] and key in seen:
            findings.append(
                {
                    "kind": "duplicate",
                    "lines": [line["id"]],
                    "of": seen[key],
                    "amount": line["you_owe"],
                }
            )
        else:
            seen[key] = line["id"]
        share = line["allowed"] - line["plan_paid"]
        if (
            bill["network"] == "in"
            and line["allowed"] > 0
            and line["you_owe"] - max(share, Decimal(0)) > TOLERANCE
        ):
            findings.append(
                {
                    "kind": "balance_billed",
                    "lines": [line["id"]],
                    "amount": line["you_owe"] - max(share, Decimal(0)),
                }
            )
    total = sum((line["you_owe"] for line in bill["lines"]), Decimal(0))
    if bill["total_you_owe"] and abs(total - bill["total_you_owe"]) > TOLERANCE:
        findings.append(
            {
                "kind": "total_mismatch",
                "lines": [],
                "amount": abs(total - bill["total_you_owe"]),
            }
        )
    return findings


def summary(bill: dict, findings: list[dict], n: int) -> str:
    """What the LLM keeps in context: which lines exist, never their amounts."""
    lines = "; ".join(
        f"{line['id']} {line['date']} {line['code']} {line['description']}"
        for line in bill["lines"]
    )
    flagged = "; ".join(
        f"{finding['kind']} on {', '.join(finding['lines']) or 'the total'}" for finding in findings
    )
    network = {"in": "in-network", "out": "out-of-network"}.get(bill["network"], "network unknown")
    return (
        f"Bill {n} is on the member's screen: {KINDS[bill['kind']]} from "
        f"{bill['provider'] or 'an unnamed provider'}, {network}. Lines: {lines}. "
        f"Checks found: {flagged or 'nothing wrong'}. No amounts are listed here: call "
        "show_lines (use TOTAL for the totals) before stating any amount."
    )


def dispute_ref(bill_n: int, line_ids: list[str]) -> str:
    """A stable, made-up reference number, so the same dispute reads the same."""
    seed = zlib.crc32(f"{bill_n}:{','.join(line_ids)}".encode())
    return f"KH-D{seed % 1_000_000:06d}"


def prepare_image(data: bytes) -> bytes:
    """Decode the upload as an image (rejecting anything else), shrink it, re-encode
    as JPEG. Re-encoding also drops EXIF, including any location in a phone photo."""
    with Image.open(io.BytesIO(data)) as probe:
        if probe.format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("Unsupported image format")
        if probe.width * probe.height > 40_000_000:
            raise ValueError("Image too large")
        probe.verify()
    with Image.open(io.BytesIO(data)) as image:
        image = image.convert("RGB")
        image.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=85)
    return out.getvalue()


def line_props(line: dict) -> dict:
    return {
        **line,
        **{k: str(line[k]) for k in ("billed", "allowed", "plan_paid", "you_owe")},
    }


def publish_ui_event(room: rtc.Room, component: str, props: dict) -> None:
    payload = json.dumps(
        {"type": "ui_event", "component": component, "action": "update", "props": props}
    ).encode("utf-8")
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except RuntimeError:
        logger.exception("failed to schedule playground ui event")
        return

    def log_failure(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception():
            logger.warning("failed to publish playground ui event")

    task.add_done_callback(log_failure)


class BillExplainer(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(
            instructions=(
                "You are Ivy, member services at Kestrel Health, a health plan. You "
                "help a member understand a medical bill or explanation of benefits "
                "they share on screen. Until a bill arrives, ask them to upload a photo "
                "of one or pick a sample on screen. Lines have ids like L1 and L2. "
                "Every time you talk about a line, call show_lines for it first, even "
                "if you saw it before: it highlights the line on the member's screen "
                "and returns its figures. Only use amounts it returns, never estimate "
                "or do your own math. Explain billed, allowed, plan paid and you owe in "
                "plain words. When a check found a problem, explain it and offer to "
                "open a dispute; call open_dispute only after the member says yes. "
                "Never give medical advice or guess what a code means beyond its "
                "description. This is a phone call: two or three short sentences, no "
                "lists, no markdown, never say a tool name. Call L4 'line 4'."
            ),
        )
        self.room = room
        self._vision = AsyncOpenAI(max_retries=0, timeout=20)
        self._bill: dict | None = None
        self._findings: list[dict] = []
        self._uploads = 0
        self._busy = False
        self._disputes: dict[str, dict] = {}
        self._tasks: set[asyncio.Task] = set()

    def meter_vision(self, usage) -> None:
        """Bill a vision request to this call; the hosted class overrides it."""

    def publish_initial(self) -> None:
        self._status("waiting")

    def _status(self, stage: str, **props) -> None:
        publish_ui_event(
            self.room, "BillStatus", {"stage": stage, "uploads": self._uploads, **props}
        )

    def watch_uploads(self) -> None:
        """Accept the member's bill on a byte stream for the rest of the call."""
        self.room.register_byte_stream_handler(UPLOAD_TOPIC, self._on_upload)

    def _on_upload(self, reader: rtc.ByteStreamReader, identity: str) -> None:
        task = asyncio.create_task(self._receive(reader, identity))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _receive(self, reader: rtc.ByteStreamReader, identity: str) -> None:
        sender = self.room.remote_participants.get(identity)
        info = reader.info
        problem = None
        if sender is None or sender.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            problem = "unknown sender"
        elif self._busy:
            problem = "Still reading the last bill. Send the next one after."
        elif self._uploads >= MAX_UPLOADS:
            problem = f"This call reads up to {MAX_UPLOADS} bills."
        elif info.mime_type not in UPLOAD_TYPES or (info.size or 0) > MAX_UPLOAD_BYTES:
            problem = "Send a JPEG, PNG or WebP photo under 4 MB."
        if problem:
            reader.close()
            if sender is not None:
                self._status("failed", message=problem)
            return
        self._busy = True
        self._uploads += 1
        started = time.monotonic()
        try:
            self._status("receiving", name=clip(info.name, 60))
            data = bytearray()
            try:
                async for chunk in reader:
                    data += chunk
                    if len(data) > MAX_UPLOAD_BYTES:
                        reader.close()
                        raise ValueError("too large")
            except (rtc.StreamError, ValueError):
                self._status("failed", message="The upload did not finish. Try again.")
                return
            self._status("reading", bytes=len(data))
            self.session.say("Got it. Reading your bill now.", add_to_chat_ctx=False)
            try:
                image = await asyncio.to_thread(prepare_image, bytes(data))
                bill = normalize(await self.read_bill(image))
            except Exception:  # noqa: BLE001 - any read failure becomes a spoken retry
                logger.warning("bill read failed")
                bill = None
            ms = int((time.monotonic() - started) * 1000)
            if not bill or not bill["readable"]:
                self._status("failed", message="That image did not read as a bill.", ms=ms)
                self.session.generate_reply(
                    instructions=(
                        "The image they sent could not be read as a medical bill. Ask for "
                        "a flatter, closer photo of the whole page, or a sample on screen."
                    )
                )
                return
            await self._load(bill, ms)
        finally:
            self._busy = False

    async def read_bill(self, image: bytes) -> dict:
        url = "data:image/jpeg;base64," + base64.b64encode(image).decode()
        response = await self._vision.chat.completions.create(
            model=VISION_MODEL,
            store=False,
            max_completion_tokens=1200,
            response_format=VISION_SCHEMA,
            messages=[
                {"role": "system", "content": VISION_PROMPT},
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": url, "detail": "high"}}],
                },
            ],
        )
        self.meter_vision(response.usage)
        return json.loads(response.choices[0].message.content)

    async def _load(self, bill: dict, ms: int) -> None:
        findings = audit(bill)
        self._bill, self._findings = bill, findings
        n = self._uploads
        publish_ui_event(
            self.room,
            "BillRead",
            {
                "n": n,
                "kind": bill["kind"],
                "provider": bill["provider"],
                "network": bill["network"],
                "lines": [line_props(line) for line in bill["lines"]],
                "total": str(bill["total_you_owe"]),
                "findings": [
                    {"kind": f["kind"], "lines": f["lines"], "amount": str(f["amount"])}
                    for f in findings
                ],
                "ms": ms,
            },
        )
        self._status("ready", ms=ms)
        # Swap the previous bill's summary for this one, so context never mixes bills.
        chat_ctx = self.chat_ctx.copy()
        chat_ctx.items = [item for item in chat_ctx.items if not item.id.startswith("bill-")]
        chat_ctx.add_message(role="system", content=summary(bill, findings, n), id=f"bill-{n}")
        await self.update_chat_ctx(chat_ctx)
        self.session.generate_reply(
            instructions=(
                "The bill just finished reading. Call show_lines with TOTAL and any lines "
                "a check flagged. Then in at most three short sentences say what the "
                "document is, what they owe in total, and whether anything looks wrong. "
                "Ask which line they want explained."
            )
        )

    def _line(self, line_id: str) -> dict | None:
        return next((line for line in self._bill["lines"] if line["id"] == line_id), None)

    @function_tool()
    async def show_lines(self, context: RunContext, line_ids: list[str]) -> str:
        """Read lines of the member's bill and highlight them on their screen.

        Call before stating any amount, date or code. Answer only from what this returns.

        line_ids: line ids such as L1 and L3, or TOTAL for the totals.
        """
        if not self._bill:
            return "No bill yet. Ask them to upload a photo or pick a sample on screen."
        wanted = [clip(i, 8).upper() for i in line_ids][:4]
        lines = [line for line in map(self._line, wanted) if line]
        if not lines and "TOTAL" not in wanted:
            known = ", ".join(line["id"] for line in self._bill["lines"])
            return f"No such line. This bill has {known}."
        publish_ui_event(
            self.room,
            "BillFocus",
            {"lines": [line["id"] for line in lines], "total": "TOTAL" in wanted},
        )
        parts = []
        for line in lines:
            parts.append(
                f"{line['id']}: {line['date']}, code {line['code']}, {line['description']}. "
                f"Billed {say_money(line['billed'])}, allowed {say_money(line['allowed'])}, "
                f"plan paid {say_money(line['plan_paid'])}, you owe {say_money(line['you_owe'])}."
                + (f" Remark: {line['remark']}." if line["remark"] else "")
            )
            for finding in self._findings:
                if line["id"] in finding["lines"]:
                    extra = f" It repeats {finding['of']}." if finding.get("of") else ""
                    parts.append(
                        f"Check on {line['id']}: {FINDINGS[finding['kind']]}{extra} "
                        f"Amount in question: {say_money(finding['amount'])}. A dispute "
                        "can be opened."
                    )
        if "TOTAL" in wanted:
            totals = {
                key: sum((line[key] for line in self._bill["lines"]), Decimal(0))
                for key in ("billed", "allowed", "plan_paid", "you_owe")
            }
            parts.append(
                f"Totals across lines: billed {say_money(totals['billed'])}, allowed "
                f"{say_money(totals['allowed'])}, plan paid {say_money(totals['plan_paid'])}, "
                f"you owe {say_money(totals['you_owe'])}. Printed total you owe: "
                f"{say_money(self._bill['total_you_owe'])}."
            )
            at_issue = sum((f["amount"] for f in self._findings), Decimal(0))
            if self._findings:
                parts.append(f"Checks flagged {say_money(at_issue)} in question.")
        parts.append(TERMS)
        return " ".join(parts)

    @function_tool()
    async def open_dispute(self, context: RunContext, line_ids: list[str], reason: str) -> str:
        """Open a billing dispute for flagged lines, only after the member agrees.

        line_ids: the flagged line ids to dispute.
        reason: the problem in a few words, as the member would put it.
        """
        if not self._bill:
            return "No bill yet."
        wanted = sorted({clip(i, 8).upper() for i in line_ids})
        flagged = [f for f in self._findings if set(f["lines"]) & set(wanted)]
        if not wanted or not flagged:
            return (
                "Those lines passed every check, so there is nothing to dispute. Explain "
                "why the amount is right instead."
            )
        ref = dispute_ref(self._uploads, wanted)
        if ref in self._disputes:
            return f"Dispute {ref} is already open for those lines."
        if len(self._disputes) >= MAX_DISPUTES:
            return "Two disputes are open on this call already. Offer to follow up by mail."
        amount = sum((f["amount"] for f in flagged), Decimal(0))
        dispute = {
            "ref": ref,
            "lines": wanted,
            "amount": str(amount),
            "reason": clip(reason, 120),
            "kinds": sorted({f["kind"] for f in flagged}),
        }
        self._disputes[ref] = dispute
        publish_ui_event(self.room, "BillDispute", dispute)
        return (
            f"Dispute {ref} opened for {say_money(amount)} on {', '.join(wanted)}. The "
            "provider is asked to correct the bill, and the member does not need to pay "
            "the disputed amount while it is reviewed (simulation: nothing is sent). "
            "Read them the reference."
        )

    async def on_exit(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await self._vision.close()


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="medical-bill-explainer")
async def entrypoint(ctx: JobContext) -> None:
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
        vad=ctx.proc.userdata["vad"],
    )
    await ctx.connect()
    agent = BillExplainer(ctx.room)
    agent.watch_uploads()
    await session.start(agent=agent, room=ctx.room)
    agent.publish_initial()
    await session.generate_reply(
        instructions=(
            "Greet them as Kestrel Health member services and ask them to send a photo of "
            "the bill they have a question about."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
