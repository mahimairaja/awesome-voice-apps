"""voice-checkout: a voice agent that fills a store's checkout page over RPC.

The shopper talks; the agent forwards each detail to the checkout page with
LiveKit RPC. The page owns the form, the prices and the validation. When it
rejects a value ("that postal code is in Ontario"), the error comes back to the
model as the tool result and the agent asks again. Edits the shopper types on
the page come back to the agent the same way, in the other direction.

Run it:
1. cp .env.example .env and fill the six keys.
2. uv sync
3. uv run python agent.py dev, and join the room from a page that registers
   the checkout.* RPC methods described in README.md.
"""

import json
import logging

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

# Fields the page accepts, in the order the agent asks for them.
FIELDS = (
    "item",
    "size",
    "colour",
    "quantity",
    "full_name",
    "street",
    "city",
    "region",
    "postal_code",
    "country",
    "shipping",
)
# A page that answers slower than this is treated as gone, so the shopper never
# waits on dead air.
RPC_TIMEOUT = 4.0
# Custom RPC error codes the page uses (1001-1999 are reserved by LiveKit).
INVALID_FIELDS = 2001
NOT_READY = 2002
MAX_PAGE_EDITS = 20

INSTRUCTIONS = """\
You are the voice checkout for Ridgeline Outfitters, a fictional outdoor gear
store. The checkout page is open on the shopper's screen and you fill it for
them with your tools. The page is the source of truth: it validates every
value and it computes the price. Never compute totals or decide validity
yourself.

The catalog:
- Summit shell jacket, 189 dollars, sizes XS to XL, colours slate, moss, ember.
- Trail daypack 24 litre, 95 dollars, one size, colours slate, sand.
- Merino base layer, 68 dollars, sizes XS to XL, colours black, heather.

How to work:
- Ask for one or two details at a time: the item with size and colour, then
  quantity, then name, then street, city, province or state, postal or ZIP
  code, then shipping (standard or express).
- When the shopper gives details, call fill_checkout right away with every
  field they gave in that answer. Pass region as the province or state name.
- If a tool returns an error, the page rejected that value. Tell the shopper
  briefly what the page said and ask for that detail again. Fields the page
  accepted stay filled.
- When the shopper typed something on the page, it shows up as a message that
  starts with "On the page:". Treat it as their answer and do not ask again.
- When every field is filled, call review_checkout, read back the item and the
  total exactly as the page states it, and ask them to confirm. Only after a
  clear yes, call place_order with that total.
- This is a simulation. Ask for made-up details. Never ask for a card number:
  payment is the demo card already on the page.
- Keep replies short, plain spoken text, no markdown, lists or emojis.
"""


def _clean(fields: dict) -> dict[str, str]:
    """Drop empty values; the page validates everything else."""
    return {
        name: str(value).strip()[:80]
        for name, value in fields.items()
        if name in FIELDS and value is not None and str(value).strip()
    }


def summarize(state: dict) -> str:
    """Turn the page's form state into a short tool result for the model."""
    filled = state.get("fields") or {}
    parts = [f"{name}: {filled[name]}" for name in FIELDS if filled.get(name)]
    missing = state.get("missing") or []
    text = "Form now has " + (", ".join(parts) if parts else "nothing yet") + "."
    if missing:
        text += " Still needed: " + ", ".join(missing) + "."
    if state.get("total"):
        text += f" The page total is {state['total']}."
    return text


class VoiceCheckout(Agent):
    def __init__(self, room: rtc.Room) -> None:
        super().__init__(instructions=INSTRUCTIONS)
        self.room = room
        self._page_edits = 0
        # The page tells us when the shopper types into the form themselves.
        room.local_participant.register_rpc_method("checkout.page_edited", self._page_edited)

    def _shopper(self) -> str:
        for participant in self.room.remote_participants.values():
            if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD:
                return participant.identity
        raise ToolError("No checkout page is connected. Ask the shopper to open it.")

    async def call_page(self, method: str, payload: dict) -> dict:
        """Forward one tool call to the page and return its JSON reply.

        Every RPC failure becomes a ToolError, so the model hears what went
        wrong instead of the call crashing.
        """
        try:
            reply = await self.room.local_participant.perform_rpc(
                destination_identity=self._shopper(),
                method=method,
                payload=json.dumps(payload),
                response_timeout=RPC_TIMEOUT,
            )
        except rtc.RpcError as error:
            if error.code in (INVALID_FIELDS, NOT_READY):
                # The page's own words, plus what it kept, go back to the model.
                try:
                    detail = f" {summarize(json.loads(error.data))}" if error.data else ""
                except (ValueError, AttributeError):
                    detail = ""
                raise ToolError(f"The page rejected it: {error.message}.{detail}") from None
            logger.warning("checkout rpc %s failed with %s", method, error.code)
            raise ToolError(
                "The checkout page did not answer. Apologise and ask the shopper "
                "to check the page is still open."
            ) from None
        try:
            return json.loads(reply)
        except ValueError:
            raise ToolError("The checkout page sent an unreadable reply.") from None

    async def _page_edited(self, data: rtc.RpcInvocationData) -> str:
        """The shopper typed a field on the page; let the model know."""
        if data.caller_identity != self._shopper() or self._page_edits >= MAX_PAGE_EDITS:
            raise rtc.RpcError(NOT_READY, "Not accepted")
        try:
            edit = json.loads(data.payload)
            field, value = edit["field"], str(edit["value"]).strip()[:80]
        except (ValueError, KeyError, TypeError):
            raise rtc.RpcError(INVALID_FIELDS, "Expected {field, value}") from None
        if field not in FIELDS:
            raise rtc.RpcError(INVALID_FIELDS, "Unknown field")
        self._page_edits += 1
        chat_ctx = self.chat_ctx.copy()
        chat_ctx.add_message(role="user", content=f"On the page: I set {field} to {value!r}.")
        await self.update_chat_ctx(chat_ctx)
        return "ok"

    @function_tool()
    async def fill_checkout(
        self,
        context: RunContext,
        item: str | None = None,
        size: str | None = None,
        colour: str | None = None,
        quantity: int | None = None,
        full_name: str | None = None,
        street: str | None = None,
        city: str | None = None,
        region: str | None = None,
        postal_code: str | None = None,
        country: str | None = None,
        shipping: str | None = None,
    ) -> str:
        """Fill one or more checkout fields on the shopper's page.

        Pass only the fields the shopper just gave. item is the product name,
        region the province or state, country Canada or United States,
        shipping standard or express.
        """
        fields = _clean(
            {
                "item": item,
                "size": size,
                "colour": colour,
                "quantity": quantity,
                "full_name": full_name,
                "street": street,
                "city": city,
                "region": region,
                "postal_code": postal_code,
                "country": country,
                "shipping": shipping,
            }
        )
        if not fields:
            raise ToolError("No fields given. Ask the shopper for the next detail.")
        return summarize(await self.call_page("checkout.set_fields", {"fields": fields}))

    @function_tool()
    async def review_checkout(self, context: RunContext) -> str:
        """Read the whole checkout from the page, including the total."""
        return summarize(await self.call_page("checkout.get_state", {}))

    @function_tool()
    async def place_order(self, context: RunContext, total: str) -> str:
        """Place the order after the shopper confirmed the read-back.

        total: the total exactly as review_checkout reported it.
        """
        result = await self.call_page("checkout.place_order", {"total": total})
        return f"Order placed. Order number {result.get('order', 'unknown')}."


server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="voice-checkout")
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    session = AgentSession(
        stt=deepgram.STT(model="nova-3"),
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=cartesia.TTS(model="sonic-2"),
        vad=ctx.proc.userdata["vad"],
    )
    await ctx.connect()
    await session.start(agent=VoiceCheckout(ctx.room), room=ctx.room)
    await session.generate_reply(
        instructions=(
            "Say you can fill the checkout by voice, that this is a simulation so "
            "they should use made-up details, and ask what they would like to buy."
        )
    )


if __name__ == "__main__":
    cli.run_app(server)
