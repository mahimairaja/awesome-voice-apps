"""returns-desk-qa: a returns desk voice agent with a live QA supervisor.

The agent handles an online store return: it verifies the order, applies the
return policy and issues a return authorization. Beside it, a second and much
cheaper pipeline grades the call while it happens: compliance items ticked,
customer sentiment per turn, and a flag (plus a whispered correction to the
agent) when the agent says something the policy does not allow.

The grader runs off the critical path. It never delays a reply; it reads the
transcript after each agent turn and its correction lands on the next turn.

Stack: Deepgram Nova-3 STT, OpenAI gpt-4o-mini LLM, gpt-4.1-mini grader, Cartesia TTS.

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py console
"""

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Callable, Literal

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

load_dotenv()

logger = logging.getLogger(__name__)

STORE = "Fernway Goods"
ZIP_CODE = "60614"
# The customer's recent orders. `days` is days since delivery.
ORDERS: dict[str, dict] = {
    "FW4821": {"item": "Linen duvet cover, queen", "price": 129, "days": 12, "final_sale": False},
    "FW5530": {"item": "Ceramic table lamp", "price": 89, "days": 41, "final_sale": False},
    "FW6017": {"item": "Clearance wool throw", "price": 39, "days": 6, "final_sale": True},
}
POLICY = (
    "1. Within 30 days of delivery: refund to the original payment method "
    "(5 to 7 business days after we receive it), a free exchange, or store credit. "
    "2. Days 31 to 60: store credit only. After 60 days: no return. "
    "3. Final sale items cannot be returned or exchanged. "
    "4. An item that arrived damaged, reported within 14 days of delivery, gets a "
    "free replacement or a refund, even on final sale. "
    "5. Every return gets a prepaid label by email. No other fees, credits or exceptions."
)
Resolution = Literal["refund", "exchange", "store_credit", "replacement"]

INSTRUCTIONS = (
    f"You are the returns desk voice agent for {STORE}, an online homeware store. "
    "Before discussing any order, ask for the order number and the ZIP code on the "
    "order and call verify_order. Then ask what is wrong and whether it arrived "
    "damaged, call check_return_options, state the policy that applies in one "
    "sentence, and offer only the options it returns. When the customer picks one, "
    "call process_return. Finish with a recap: the return number, that a prepaid "
    "label is on its way by email, and when the money or item arrives. Be warm when "
    "the customer is upset. Never promise anything the tools did not allow, never "
    "invent fees, credits or exceptions, and never ask for card numbers or passwords. "
    "A system message starting with 'QA supervisor' is a private note from your "
    "supervisor: follow it, and if it says you misspoke, correct yourself briefly. "
    "Keep replies to one or two short sentences, plain text, no markdown."
)
GREETING = (
    f"Greet the customer as the {STORE} returns desk, say the call is monitored for "
    "quality, and ask for their order number and ZIP code."
)


def publish_ui_event(
    room: rtc.Room,
    component: str,
    action: Literal["mount", "update", "unmount"],
    props: dict | None = None,
    component_id: str | None = None,
) -> None:
    envelope = {"type": "ui_event", "component": component, "action": action, "props": props or {}}
    if component_id is not None:
        envelope["id"] = component_id
    try:
        payload = json.dumps(envelope).encode("utf-8")
    except (TypeError, ValueError):
        logger.exception("failed to encode playground ui event")
        return
    try:
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except RuntimeError:
        logger.exception("failed to schedule playground ui event")
        return

    def log_publish_failure(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("failed to publish playground ui event")

    task.add_done_callback(log_publish_failure)


def normalize_order(value: str) -> str:
    """'f w 4 8 2 1', 'FW-4821' and '4821' all become 'FW4821'."""
    v = re.sub(r"[^A-Z0-9]", "", value.upper())
    return v if v.startswith("FW") else f"FW{v}"


def return_options(order: dict, damaged: bool) -> tuple[list[str], str]:
    """The resolutions the policy allows for this order, and the rule that applies."""
    days = order["days"]
    if damaged and days <= 14:
        return ["replacement", "refund"], "arrived damaged and reported within 14 days"
    if order["final_sale"]:
        return [], "final sale items cannot be returned or exchanged"
    if days <= 30:
        return ["refund", "exchange", "store_credit"], f"delivered {days} days ago, within 30"
    if days <= 60:
        return ["store_credit"], f"delivered {days} days ago, so store credit only"
    return [], f"delivered {days} days ago, past the 60 day limit"


def initial_state() -> dict:
    return {"verified": [], "options": {}, "returns": {}}


def describe_facts(state: dict) -> str:
    """What the tools established so far, in words the grader can check lines against."""
    lines = []
    for number in state["verified"]:
        order = ORDERS[number]
        sale = "final sale, " if order["final_sale"] else ""
        options = state["options"].get(number)
        allowed = (
            "options not checked yet"
            if options is None
            else f"allowed: {', '.join(options)}"
            if options
            else "allowed: nothing, no return possible"
        )
        issued = state["returns"].get(number)
        done = f", issued {issued['rma']} ({issued['resolution']})" if issued else ""
        lines.append(
            f"{number} verified: {order['item']}, {sale}delivered {order['days']} days ago, "
            f"{allowed}{done}."
        )
    return " ".join(lines) or "No order verified yet."


def publish_orders(room: rtc.Room, state: dict) -> None:
    rows = []
    for number, order in ORDERS.items():
        rows.append(
            {
                "id": number,
                "item": order["item"],
                "price": order["price"],
                "days": order["days"],
                "final_sale": order["final_sale"],
                "verified": number in state["verified"],
                "options": state["options"].get(number, []),
                "rma": state["returns"].get(number, {}).get("rma", ""),
                "resolution": state["returns"].get(number, {}).get("resolution", ""),
            }
        )
    publish_ui_event(
        room, "Orders", "update", component_id="orders", props={"zip": ZIP_CODE, "orders": rows}
    )


# ---------------------------------------------------------------- QA supervisor

QA_ITEMS = {
    "disclosure": "Monitoring disclosed",
    "verified": "Order verified first",
    "policy": "Policy stated",
    "empathy": "Customer acknowledged",
    "resolution": "Resolution confirmed",
    "recap": "Next steps recapped",
}
# Items the grader judges from the transcript; the rest come from tool results.
GRADED = ("disclosure", "policy", "empathy", "recap")
VIOLATIONS = (
    "none",
    "off_policy_promise",
    "sensitive_data_request",
    "unverified_action",
    "dismissive_tone",
)
GRADER_PROMPT = (
    f"You are a QA supervisor scoring a live {STORE} returns call. The return policy: "
    f"{POLICY} You get the tool facts (the system of record) and the transcript; "
    "judge only the lines after '--- new ---', using earlier lines as context. "
    "Answer in JSON. check: first, in under 25 words, compare what the agent said "
    "with the tool facts. evidence: which of these the AGENT did in the new lines: "
    "disclosure (said the call is monitored or recorded), policy (stated the specific "
    "return rule that applies to the order), empathy (acknowledged the customer's "
    "problem or feelings), recap (gave the return number and next steps). "
    "sentiment: the CUSTOMER's latest new line, from -1 (angry) to 1 (delighted): "
    "demanding or annoyed is about -0.5, plain facts are 0, thanks is about 0.5; 0 "
    "if the customer has no new line. violation: the worst clear problem in the "
    "AGENT's new lines, else none: off_policy_promise (offers a refund, credit, fee "
    "waiver or exception the policy and tool facts do not allow), "
    "sensitive_data_request (asks for a card number, password or security answer), "
    "unverified_action (states an order's item, price or eligibility while that "
    "order is not in verified_orders; asking for the order number or ZIP is fine), "
    "dismissive_tone (rude or blames the customer). Refusing politely is never a "
    "violation, and a refusal that matches the tool facts is correct. When unsure, answer none. quote: the agent's exact words behind the "
    "violation, else empty. coaching: one short instruction the agent can act on in "
    "its next reply, else empty. Never coach the agent to offer anything the tool "
    "facts do not allow."
)
GRADE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "qa_grade",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["check", "evidence", "sentiment", "violation", "quote", "coaching"],
            "properties": {
                "check": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string", "enum": list(GRADED)}},
                "sentiment": {"type": "number"},
                "violation": {"type": "string", "enum": list(VIOLATIONS)},
                "quote": {"type": "string"},
                "coaching": {"type": "string"},
            },
        },
    },
}


def parse_grade(raw: str) -> dict:
    """Validate the grader's JSON; anything off-schema becomes a neutral grade."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = {}
    evidence = [e for e in data.get("evidence", []) if e in GRADED]
    sentiment = data.get("sentiment", 0)
    if not isinstance(sentiment, (int, float)) or isinstance(sentiment, bool):
        sentiment = 0
    violation = data.get("violation", "none")
    if violation not in VIOLATIONS:
        violation = "none"
    return {
        "evidence": evidence,
        "sentiment": max(-1.0, min(1.0, float(sentiment))),
        "violation": violation,
        "quote": str(data.get("quote", ""))[:160],
        "coaching": str(data.get("coaching", ""))[:200],
    }


class QaSupervisor:
    """Grades the call beside the voice pipeline, never in front of it.

    After each agent turn it sends the last few lines to a cheap model, ticks the
    compliance items the agent just covered, plots the customer's sentiment and
    flags off-policy lines. A flag queues a private note the agent reads on its
    next turn. Grades are serialized and capped per call.
    """

    def __init__(
        self,
        room: rtc.Room,
        facts: Callable[[], str],
        on_usage: Callable[[int, int], None] | None = None,
        max_grades: int = 20,
        model: str = "gpt-4.1-mini",
    ) -> None:
        self.room = room
        self.facts = facts
        self.on_usage = on_usage
        self.max_grades = max_grades
        self.model = model
        self.started = time.monotonic()
        self.items = {key: None for key in QA_ITEMS}
        self.sentiment: list[dict] = []
        self.flags: list[dict] = []
        self.lines: list[tuple[int, str, str]] = []
        self._seq = 0
        self._graded = 0
        self.grades = 0
        self.latency_ms = 0
        self.whisper: str | None = None
        self._customer_spoke = False
        self._pending = False
        self._running: asyncio.Task | None = None
        self._client = None

    def elapsed(self) -> float:
        return round(time.monotonic() - self.started, 1)

    def attach(self, session: AgentSession) -> None:
        session.on("conversation_item_added", lambda event: self.observe(event.item))

    def observe(self, item) -> None:
        role = getattr(item, "role", None)
        text = (getattr(item, "text_content", None) or "").strip()
        if role not in ("user", "assistant") or not text:
            return
        self._seq += 1
        who = "Customer" if role == "user" else "Agent"
        self.lines = [*self.lines, (self._seq, who, text[:400])][-8:]
        if role == "user":
            self._customer_spoke = True
            return
        self._pending = True
        if not self._running or self._running.done():
            self._running = asyncio.create_task(self._drain())

    def mark(self, key: str) -> None:
        """Tick an item from a tool result (the system of record, not the model)."""
        if self.items.get(key) is None:
            self.items[key] = self.elapsed()
            self.publish()

    def guard(self, quote: str, note: str) -> None:
        """A tool refused an action; record it so QA shows the backstop working."""
        self.flags.append(
            {
                "kind": "guard",
                "quote": quote,
                "note": note,
                "at": self.elapsed(),
                "whispered": False,
            }
        )
        self.publish()

    def take_whisper(self) -> str | None:
        note, self.whisper = self.whisper, None
        flagged = [flag for flag in self.flags if flag["kind"] != "guard"]
        if note and flagged:
            flagged[-1]["whispered"] = True
            self.publish()
        return note

    async def _drain(self) -> None:
        while self._pending and self.grades < self.max_grades:
            self._pending = False
            try:
                await self._grade()
            except Exception:
                logger.warning("QA grade failed; the call continues ungraded")

    async def _grade(self) -> None:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(max_retries=0, timeout=8)
        self.grades += 1
        spoke, self._customer_spoke = self._customer_spoke, False
        old = [f"{who}: {text}" for seq, who, text in self.lines if seq <= self._graded]
        new = [f"{who}: {text}" for seq, who, text in self.lines if seq > self._graded]
        self._graded = self._seq
        transcript = "\n".join([*old, "--- new ---", *new])
        begin = time.monotonic()
        response = await self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": GRADER_PROMPT},
                {"role": "user", "content": f"Tool facts: {self.facts()}\n\n{transcript}"},
            ],
            response_format=GRADE_SCHEMA,
            max_completion_tokens=160,
            temperature=0,
            store=False,
        )
        self.latency_ms = int((time.monotonic() - begin) * 1000)
        if response.usage and self.on_usage:
            self.on_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
        grade = parse_grade(response.choices[0].message.content)
        at = self.elapsed()
        for key in grade["evidence"]:
            if self.items[key] is None:
                self.items[key] = at
        if spoke:
            self.sentiment.append({"at": at, "score": round(grade["sentiment"], 2)})
        if grade["violation"] != "none":
            self.flags.append(
                {
                    "kind": grade["violation"],
                    "quote": grade["quote"],
                    "note": grade["coaching"],
                    "at": at,
                    "whispered": False,
                }
            )
            self.whisper = grade["coaching"] or "Correct your last statement to match the policy."
        self.publish()

    def score(self) -> int:
        done = sum(1 for value in self.items.values() if value is not None)
        misses = sum(1 for flag in self.flags if flag["kind"] != "guard")
        return max(0, round(100 * done / len(QA_ITEMS)) - 15 * misses)

    def publish(self) -> None:
        publish_ui_event(
            self.room,
            "QaBoard",
            "update",
            component_id="qa",
            props={
                "score": self.score(),
                "items": [
                    {"id": key, "label": label, "at": self.items[key]}
                    for key, label in QA_ITEMS.items()
                ],
                "sentiment": self.sentiment[-24:],
                "flags": self.flags[-6:],
                "grades": self.grades,
                "latency_ms": self.latency_ms,
            },
        )

    async def aclose(self) -> None:
        if self._running:
            self._running.cancel()
        if self._client is not None:
            await self._client.close()


# ---------------------------------------------------------------- the agent


class ReturnsDesk(Agent):
    def __init__(self, room: rtc.Room, max_grades: int = 20) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self.qa = QaSupervisor(room, self.tool_facts, self.record_qa_usage, max_grades)

    def tool_facts(self) -> str:
        state = self.session.userdata
        return describe_facts(state)

    def record_qa_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        logger.info("qa grade used %s input and %s output tokens", prompt_tokens, completion_tokens)

    async def on_enter(self) -> None:
        self.qa.attach(self.session)
        publish_orders(self.room, self.session.userdata)
        self.qa.publish()
        await self.session.generate_reply(instructions=GREETING)

    async def on_exit(self) -> None:
        await self.qa.aclose()

    async def llm_node(self, chat_ctx, tools, model_settings):
        # The supervisor's correction reaches the agent before its next reply.
        note = self.qa.take_whisper()
        if note:
            chat_ctx.add_message(role="system", content=f"QA supervisor: {note}")
        async for chunk in super().llm_node(chat_ctx, tools, model_settings):
            yield chunk

    @function_tool()
    async def verify_order(
        self, context: RunContext[dict], order_number: str, zip_code: str
    ) -> str:
        """Verify the caller owns an order before discussing it.

        order_number: like FW4821 (digits alone are fine). zip_code: the ZIP on the order.
        """
        number = normalize_order(order_number)
        if number not in ORDERS or re.sub(r"\D", "", zip_code) != ZIP_CODE:
            return "not verified: the order number and ZIP code do not match. Ask again."
        state = context.userdata
        if number not in state["verified"]:
            state["verified"].append(number)
        self.qa.mark("verified")
        publish_orders(self.room, state)
        order = ORDERS[number]
        sale = ", final sale" if order["final_sale"] else ""
        return f"verified {number}: {order['item']}, ${order['price']}{sale}, delivered {order['days']} days ago"

    @function_tool()
    async def check_return_options(
        self, context: RunContext[dict], order_number: str, damaged: bool
    ) -> str:
        """Look up what the return policy allows for a verified order.

        damaged: true only if the customer says the item arrived damaged.
        """
        number = normalize_order(order_number)
        state = context.userdata
        if number not in state["verified"]:
            self.qa.guard(f"options for {number}", "Blocked: order not verified yet")
            return "refused: verify the order first"
        options, rule = return_options(ORDERS[number], damaged)
        state["options"][number] = options
        publish_orders(self.room, state)
        if not options:
            return f"no return possible: {rule}. Explain this kindly; offer nothing else."
        return f"allowed: {', '.join(options)} ({rule}). Offer only these."

    @function_tool()
    async def process_return(
        self, context: RunContext[dict], order_number: str, resolution: Resolution
    ) -> str:
        """Issue the return once the customer chooses an allowed option."""
        number = normalize_order(order_number)
        state = context.userdata
        if number not in state["verified"]:
            self.qa.guard(f"{resolution} on {number}", "Blocked: order not verified yet")
            return "refused: verify the order first"
        if resolution not in state["options"].get(number, []):
            self.qa.guard(f"{resolution} on {number}", "Blocked: not allowed by the policy")
            allowed = ", ".join(state["options"].get(number, [])) or "none"
            return f"refused: {resolution} is not allowed. Allowed: {allowed}. Check options first."
        issued = state["returns"].get(number)
        if not issued:
            issued = {"rma": f"RMA-{uuid.uuid4().hex[:6].upper()}", "resolution": resolution}
            state["returns"][number] = issued
        self.qa.mark("resolution")
        publish_orders(self.room, state)
        return (
            f"issued {issued['rma']} for {issued['resolution']}. A prepaid label goes to "
            "their email. Refunds land 5 to 7 business days after we receive the item."
        )


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="returns-desk-qa")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    session = AgentSession(
        userdata=initial_state(),
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3"),
        vad=ctx.proc.userdata["vad"],
    )
    await ctx.connect()
    await session.start(agent=ReturnsDesk(ctx.room), room=ctx.room)


if __name__ == "__main__":
    cli.run_app(server)
