"""manager-approval: a refund line that asks a human manager before it pays out.

A customer of Harbor & Pine, a made-up home goods store, wants $649 back for
an espresso machine that broke on day 41. The returns agent may refund up to
$150 inside 30 days on its own; anything else needs a manager. So it:

1. sends the manager a short whisper brief over LiveKit RPC (the manager
   console is a page in the room),
2. keeps the caller company on hold, with timed check-ins and a timeout that
   falls back to written policy if nobody answers,
3. acts on the manager's decision as soon as the console calls back over RPC:
   approve, counter with store credit, decline, or take the call,
4. on "take the call", warm-transfers to the manager with the case attached,
   so the caller never repeats themselves.

Here the manager is a second voice on the same call. In production the manager
is a person, dialled with LiveKit's WarmTransferTask; the approval handshake
and the brief stay the same.

Stack: Deepgram nova-3 STT, OpenAI gpt-4o-mini, Cartesia sonic-3 (two voices).

Run it:
1. cp .env.example .env and fill the keys.
2. uv sync
3. uv run python agent.py dev, and join the room from a page that answers
   approval.request and calls approval.decide (see README.md). Without one,
   every request falls back to the policy manager.
"""

import asyncio
import json
import logging
import secrets
import time
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
    ToolError,
    cli,
    function_tool,
)
from livekit.plugins import cartesia, deepgram, openai, silero

load_dotenv()

logger = logging.getLogger(__name__)

STORE = "Harbor & Pine"
ORDER = {
    "number": "HP-4821",
    "customer": "Alex Morgan",
    "item": "Aria Pro espresso machine",
    "price": 649.0,
    "days_since_delivery": 41,
    "card": "0912",
}
# What the agent may refund on its own, and what only a manager may.
AGENT_LIMIT = 150.0
RETURN_WINDOW_DAYS = 30
DEFECT_WINDOW_DAYS = 90
MANAGER_LIMIT = 1000.0
MANAGER = "Dana"
# Two Cartesia stock voices, so the caller hears the transfer.
AGENT_VOICE = "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
MANAGER_VOICE = "a167e0f3-df7e-4d52-a9c3-f949145efdab"

# Hold handling: say something at these seconds on hold, then stop waiting.
CHECK_INS = (
    (12, "Still here with you. My manager has your case open now."),
    (28, "Thanks for holding. I flagged it as a defect, which usually helps."),
)
HOLD_TIMEOUT = 45
# A console that answers slower than this is treated as offline.
RPC_TIMEOUT = 4.0
# Custom RPC error codes (1001-1999 are reserved by LiveKit).
REJECTED = 2001
REASONS = ("defective", "damaged", "changed_mind", "late_delivery", "other")
VERDICTS = ("approve", "counter", "decline", "take_call")
MAX_EVENTS = 12


# --- The case -------------------------------------------------------------


def new_case(now: float | None = None) -> dict:
    """Per-call state, published to the page as one snapshot."""
    return {
        "stage": "intake",
        "started": now if now is not None else time.time(),
        "order": dict(ORDER),
        "limits": {
            "agent": AGENT_LIMIT,
            "window_days": RETURN_WINDOW_DAYS,
            "manager": MANAGER_LIMIT,
        },
        "found": False,
        "request": None,
        "hold": None,
        "decision": None,
        "approved": None,
        "refund": None,
        "handoff": [],
        "events": [],
    }


def log_event(case: dict, kind: str, text: str) -> None:
    case["events"].append(
        {"t": round(max(0.0, time.time() - case["started"]), 1), "kind": kind, "text": text}
    )
    del case["events"][:-MAX_EVENTS]


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def within_own_limit(amount: float) -> bool:
    return amount <= AGENT_LIMIT and ORDER["days_since_delivery"] <= RETURN_WINDOW_DAYS


def policy_decision(request: dict) -> dict:
    """The store's written policy: what the simulated manager decides."""
    days = ORDER["days_since_delivery"]
    if request["amount"] > ORDER["price"]:
        return {"verdict": "decline", "note": "That is more than the order cost."}
    if days > DEFECT_WINDOW_DAYS:
        return {"verdict": "decline", "note": f"Past the {DEFECT_WINDOW_DAYS}-day defect window."}
    if request["reason"] in ("defective", "damaged"):
        return {
            "verdict": "approve",
            "note": f"Defect inside {DEFECT_WINDOW_DAYS} days: refund to the original card.",
        }
    return {
        "verdict": "counter",
        "note": f"Past {RETURN_WINDOW_DAYS} days and not a defect: store credit only.",
    }


def whisper(case: dict, request: dict) -> dict:
    """The brief the manager reads before deciding: one line, then the facts."""
    order = case["order"]
    days = order["days_since_delivery"]
    late = days - RETURN_WINDOW_DAYS
    to = "the card" if request["to"] == "card" else "store credit"
    advice = policy_decision(request)["verdict"]
    line = (
        f"{order['customer']}, {order['item']}, {money(order['price'])}, day {days}. "
        f"{request['detail']}. Wants {money(request['amount'])} to {to}. "
        f"I'd {advice.replace('_', ' ')}."
    )
    rows = [
        {"label": "Customer", "value": f"{order['customer']}, order {order['number']}"},
        {"label": "Item", "value": f"{order['item']}, {money(order['price'])}"},
        {"label": "Ask", "value": f"{money(request['amount'])} to {to}"},
        {"label": "Reason", "value": f"{request['reason'].replace('_', ' ')}: {request['detail']}"},
        {
            "label": "Over my limit",
            "value": (
                f"{money(request['amount'])} vs {money(AGENT_LIMIT)}"
                + (f", {late} days past the window" if late > 0 else "")
            ),
        },
        {"label": "Recommend", "value": advice.replace("_", " ")},
    ]
    return {"line": line[:280], "rows": rows}


def handoff_context(case: dict) -> list[dict]:
    """What travels with the warm transfer, shown on screen as it goes."""
    request = case["request"] or {}
    decision = case["decision"] or {}
    rows = [
        {"label": "Brief", "value": (request.get("brief") or {}).get("line", "None")},
        {"label": "Order", "value": f"{ORDER['number']}, looked up and checked"},
        {
            "label": "Decision so far",
            "value": decision.get("verdict", "none").replace("_", " "),
        },
        {"label": "Transcript", "value": "Last 8 turns, tool calls dropped"},
    ]
    return rows


def refund(case: dict, amount: float, to: str, authority: Literal["agent", "manager"]) -> dict:
    """Issue the refund if this authority may, else raise ToolError with why."""
    if case["refund"]:
        raise ToolError(f"Refund {case['refund']['ref']} is already issued. Do not issue another.")
    if not case["found"]:
        raise ToolError("Look up the order first.")
    if not 0 < amount <= ORDER["price"]:
        raise ToolError(f"The amount must be between $0 and {money(ORDER['price'])}.")
    approved = case["approved"]
    if authority == "manager":
        allowed = amount <= MANAGER_LIMIT
    else:
        allowed = within_own_limit(amount) or bool(
            approved and amount <= approved["amount"] and to == approved["to"]
        )
    if not allowed:
        raise ToolError(
            "That refund is over your limit and not approved. Call ask_manager instead."
        )
    case["refund"] = {
        "ref": "RF-" + secrets.token_hex(3).upper(),
        "amount": amount,
        "to": to,
        "by": authority,
    }
    case["stage"] = "refunded"
    log_event(case, "refund", f"{money(amount)} to {to.replace('_', ' ')} by the {authority}")
    return case["refund"]


def refund_result(issued: dict) -> str:
    to = f"the card ending {ORDER['card']}" if issued["to"] == "card" else "store credit"
    return (
        f"Refund {issued['ref']} issued: {money(issued['amount'])} to {to}. Read the "
        "reference back, say cards take three to five business days, and ask if "
        "there is anything else."
    )


# --- UI events ------------------------------------------------------------


def publish_case(room: rtc.Room, case: dict) -> None:
    """Send the whole case as one snapshot on the "ui" topic."""
    envelope = {
        "type": "ui_event",
        "component": "Approval",
        "action": "update",
        "id": "approval",
        "props": {key: value for key, value in case.items() if key != "started"},
    }
    try:
        payload = json.dumps(envelope).encode()
        task = asyncio.create_task(
            room.local_participant.publish_data(payload, topic="ui", reliable=True)
        )
    except (RuntimeError, TypeError, ValueError):
        logger.exception("failed to publish the approval case")
        return
    task.add_done_callback(lambda t: t.cancelled() or t.exception())


# --- The returns agent ----------------------------------------------------

DESK_INSTRUCTIONS = f"""\
You are Sky, the returns line for {STORE}, a made-up home goods store. This
is a simulation: nothing is charged or refunded. The caller's order is on
their screen.

Your authority: refunds up to {money(AGENT_LIMIT)}, and only within
{RETURN_WINDOW_DAYS} days of delivery. Anything else needs your manager,
{MANAGER}.

1. Ask what went wrong and for the order number. Call find_order.
2. If the refund fits your authority, call issue_refund.
3. If not, say you need your manager's sign-off and you will stay with them,
   then call ask_manager with the amount, card or store_credit, the reason
   and a short description in the caller's words.
4. While you wait, keep them company: warm, short, honest. Never guess or
   hint at the outcome. Answer questions about the order or the policy.
5. The manager's decision arrives as a system message. Act on it at once.
   If the caller refuses a counteroffer or a decline, or asks for a manager,
   call transfer_to_manager.

Never invent order details or promise anything the tools did not return.
Plain spoken sentences, no lists, no markdown.
"""


class RefundDesk(Agent):
    def __init__(self, room: rtc.Room, **options) -> None:
        super().__init__(instructions=DESK_INSTRUCTIONS, **options)
        self.room = room
        self._hold_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        # The manager console calls back here with a decision.
        room.local_participant.register_rpc_method("approval.decide", self._console_decided)

    # Hooks the hosted worker overrides to keep its per-call limits.
    def make_manager(self, case: dict) -> "StoreManager":
        return StoreManager(
            self.room,
            case,
            chat_ctx=self._carried_context(),
            tts=cartesia.TTS(model="sonic-3", voice=MANAGER_VOICE),
        )

    def _carried_context(self):
        return self.chat_ctx.copy(exclude_function_call=True, exclude_instructions=True).truncate(
            max_items=8
        )

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @property
    def case(self) -> dict:
        return self.session.userdata

    def _console(self) -> str | None:
        for participant in self.room.remote_participants.values():
            if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD:
                return participant.identity
        return None

    @function_tool()
    async def find_order(self, context: RunContext[dict], order_number: str) -> str:
        """Look up the caller's order. Pass the number as they said it."""
        case = context.userdata
        digits = "".join(ch for ch in order_number if ch.isdigit())
        if digits != ORDER["number"].split("-")[1]:
            return f"No order {order_number}. Ask them to read the number on their screen."
        case["found"] = True
        if case["stage"] == "intake":
            case["stage"] = "found"
        log_event(case, "lookup", f"Order {ORDER['number']} found")
        publish_case(self.room, case)
        late = ORDER["days_since_delivery"] - RETURN_WINDOW_DAYS
        return (
            f"Order {ORDER['number']} for {ORDER['customer']}: {ORDER['item']}, "
            f"{money(ORDER['price'])}, delivered {ORDER['days_since_delivery']} days ago, "
            f"paid with the card ending {ORDER['card']}. That is {late} days past the "
            f"return window and over your {money(AGENT_LIMIT)} limit, so a full refund "
            "needs your manager."
        )

    @function_tool()
    async def issue_refund(
        self,
        context: RunContext[dict],
        amount: float,
        to: Literal["card", "store_credit"],
    ) -> str:
        """Refund the caller. Only within your authority or what the manager approved."""
        issued = refund(context.userdata, amount, to, "agent")
        publish_case(self.room, context.userdata)
        return refund_result(issued)

    @function_tool()
    async def ask_manager(
        self,
        context: RunContext[dict],
        amount: float,
        to: Literal["card", "store_credit"],
        reason: Literal["defective", "damaged", "changed_mind", "late_delivery", "other"],
        detail: str,
    ) -> str:
        """Ask the manager to approve a refund over your limit.

        detail: what happened, in a few of the caller's own words.
        """
        case = context.userdata
        if not case["found"]:
            raise ToolError("Look up the order first.")
        if case["request"]:
            return "The manager already has this request. Keep the caller company."
        if not 0 < amount <= ORDER["price"]:
            raise ToolError(f"The amount must be between $0 and {money(ORDER['price'])}.")
        request = {
            "id": "REQ-" + secrets.token_hex(2).upper(),
            "amount": round(amount, 2),
            "to": to,
            "reason": reason,
            "detail": " ".join(detail.split())[:90] or "No detail given",
        }
        request["brief"] = whisper(case, request)
        case["request"] = request
        case["stage"] = "waiting"
        case["hold"] = {"since": round(time.time() - case["started"], 1), "check_ins": 0}
        log_event(case, "request", f"{request['id']} sent with the brief")
        publish_case(self.room, case)
        delivered = await self._send_to_console(request)
        if not delivered:
            log_event(case, "fallback", "No console answered; the store policy decides")
            self._spawn(self._decide("policy", **policy_decision(request), delay=1.5))
        else:
            self._hold_task = self._spawn(self._hold(request["id"]))
        return (
            "The manager has the request. Tell the caller you are checking with your "
            "manager and will stay on the line with them. One or two sentences."
        )

    @function_tool()
    async def transfer_to_manager(self, context: RunContext[dict]):
        """Bring the manager onto the call with the case attached."""
        case = context.userdata
        if not case["found"]:
            raise ToolError("Look up the order first.")
        if case["refund"]:
            return "The refund is already issued. There is nothing left for the manager."
        return self._transfer(case)

    def _transfer(self, case: dict) -> "StoreManager":
        self._stop_hold()
        case["handoff"] = handoff_context(case)
        case["stage"] = "transferred"
        log_event(case, "transfer", f"Warm transfer to {MANAGER} with the brief")
        publish_case(self.room, case)
        return self.make_manager(case)

    async def _send_to_console(self, request: dict) -> bool:
        identity = self._console()
        if not identity:
            return False
        try:
            reply = await self.room.local_participant.perform_rpc(
                destination_identity=identity,
                method="approval.request",
                payload=json.dumps(
                    {
                        "id": request["id"],
                        "amount": request["amount"],
                        "to": request["to"],
                        "brief": request["brief"],
                        "timeout": HOLD_TIMEOUT,
                    }
                ),
                response_timeout=RPC_TIMEOUT,
            )
            return json.loads(reply).get("received") is True
        except (rtc.RpcError, ValueError, AttributeError) as error:
            logger.warning("manager console did not take the request: %s", error)
            return False

    async def _hold(self, request_id: str) -> None:
        """Keep the caller company while the manager decides."""
        case = self.case
        waited = 0.0
        for at, line in CHECK_INS:
            await asyncio.sleep(at - waited)
            waited = at
            if case["decision"] or (case["request"] or {}).get("id") != request_id:
                return
            # Never talk over the caller or over an answer already under way.
            if (
                self.session.agent_state in ("listening", "idle")
                and self.session.user_state != "speaking"
            ):
                case["hold"]["check_ins"] += 1
                log_event(case, "hold", "Check-in with the caller")
                publish_case(self.room, case)
                self.session.say(line)
        await asyncio.sleep(HOLD_TIMEOUT - waited)
        if not case["decision"]:
            log_event(case, "timeout", f"No answer in {HOLD_TIMEOUT}s; the store policy decides")
            await self._decide("policy", **policy_decision(case["request"]))

    async def on_exit(self) -> None:
        # A transfer or a hang-up ends the hold; nobody is left to keep company.
        self._stop_hold()

    def _stop_hold(self) -> None:
        if self._hold_task and self._hold_task is not asyncio.current_task():
            self._hold_task.cancel()
        self._hold_task = None

    async def _console_decided(self, data: rtc.RpcInvocationData) -> str:
        """The manager decided on the console."""
        case = self.case
        if data.caller_identity != self._console():
            raise rtc.RpcError(REJECTED, "Not the manager console")
        try:
            body = json.loads(data.payload)
            request_id, verdict = body["id"], body["verdict"]
        except (ValueError, KeyError, TypeError):
            raise rtc.RpcError(REJECTED, "Expected {id, verdict}") from None
        request = case["request"]
        if not request or request["id"] != request_id or case["decision"]:
            raise rtc.RpcError(REJECTED, "No open request with that id")
        if verdict == "auto":
            log_event(case, "console", "Left to the policy manager")
            self._spawn(self._decide("policy", **policy_decision(request), delay=2.0))
            return json.dumps({"ok": True})
        if verdict not in VERDICTS:
            raise rtc.RpcError(REJECTED, "Unknown verdict")
        note = " ".join(str(body.get("note") or "").split())[:120]
        self._spawn(self._decide("you", verdict, note))
        return json.dumps({"ok": True})

    async def _decide(self, by: str, verdict: str, note: str, delay: float = 0.0) -> None:
        """Record the decision and resume the call on it."""
        if delay:
            await asyncio.sleep(delay)
        case = self.case
        request = case["request"]
        if case["decision"] or not request:
            return
        self._stop_hold()
        waited = round(time.time() - case["started"] - case["hold"]["since"], 1)
        case["decision"] = {"by": by, "verdict": verdict, "note": note, "after": waited}
        case["stage"] = "decided"
        if verdict == "approve":
            case["approved"] = {"amount": request["amount"], "to": request["to"]}
        elif verdict == "counter":
            case["approved"] = {"amount": request["amount"], "to": "store_credit"}
        who = MANAGER if by == "you" else "the store policy"
        log_event(case, "decision", f"{verdict.replace('_', ' ').capitalize()} by {who}")
        publish_case(self.room, case)

        # The caller may be mid-sentence; the decision waits for the floor,
        # but a hold check-in in progress is cut short.
        if self.session.agent_state == "speaking":
            self.session.interrupt()
        if verdict == "take_call":
            await self.session.say(
                f"Good news, {MANAGER} wants to talk to you herself. I've told her "
                "everything, so you won't need to repeat it. Connecting you now.",
                allow_interruptions=False,
            )
            self.session.update_agent(self._transfer(case))
            return
        reason = f" Their note: {note}." if note else ""
        amount, to = money(request["amount"]), request["to"].replace("_", " ")
        message = {
            "approve": f"Manager decision: APPROVED {amount} to {to}.{reason}",
            "counter": f"Manager decision: COUNTEROFFER {amount} as store credit, "
            f"not to the card.{reason}",
            "decline": f"Manager decision: DECLINED.{reason}",
        }[verdict]
        next_step = {
            "approve": "Tell the caller it is approved, then call issue_refund for that "
            "amount and method right away.",
            "counter": "Offer the store credit plainly. If they accept, call "
            "issue_refund with store_credit. If not, offer to put the manager on.",
            "decline": "Tell the caller it was declined and why, then offer to put "
            "the manager on the line.",
        }[verdict]
        chat_ctx = self.chat_ctx.copy()
        chat_ctx.add_message(role="system", content=message)
        await self.update_chat_ctx(chat_ctx)
        self.session.generate_reply(instructions=next_step)


# --- The manager, after a warm transfer --------------------------------------


def manager_instructions(case: dict) -> str:
    rows = "; ".join(f"{row['label']}: {row['value']}" for row in case["handoff"])
    return (
        f"You are {MANAGER}, the store manager at {STORE}, a made-up store. Sky, the "
        f"returns agent, just transferred this caller to you with a brief. {rows}. "
        f"You can refund up to {money(MANAGER_LIMIT)} to the card or as store credit. "
        "Never ask the caller to repeat the order number or the problem: you have "
        "both. Decide quickly and kindly, call issue_refund, and read back the "
        "reference. This is a simulation. Plain spoken sentences, no lists."
    )


class StoreManager(Agent):
    def __init__(self, room: rtc.Room, case: dict, **options) -> None:
        super().__init__(instructions=manager_instructions(case), **options)
        self.room = room

    async def on_enter(self) -> None:
        self.session.generate_reply(
            instructions=f"Introduce yourself as {MANAGER}, the manager. In one breath, "
            "show you know the case: the item, what broke, how long they've had it. "
            "Then say what you can do for them."
        )

    @function_tool()
    async def issue_refund(
        self,
        context: RunContext[dict],
        amount: float,
        to: Literal["card", "store_credit"],
    ) -> str:
        """Refund the caller, within the manager's authority."""
        issued = refund(context.userdata, amount, to, "manager")
        publish_case(self.room, context.userdata)
        return refund_result(issued)


GREETING = f"Say you are Sky from {STORE} returns, and ask how you can help today. One sentence."

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="manager-approval")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    case = new_case()
    session = AgentSession(
        userdata=case,
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-3", voice=AGENT_VOICE),
        vad=ctx.proc.userdata["vad"],
    )
    await ctx.connect()
    await session.start(agent=RefundDesk(ctx.room), room=ctx.room)
    publish_case(ctx.room, case)
    await session.generate_reply(instructions=GREETING)


if __name__ == "__main__":
    cli.run_app(server)
