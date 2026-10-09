"""The silent copilot: hears both sides of a sales call and only writes to the screen.

For every prospect line it does two things, in order, and times both from the
moment the prospect stopped talking:

1. Retrieval. Embed the line and match it against a playbook of objection
   handlers, competitor battle cards and buying signals. A match lands on
   screen as soon as it is found, usually in a few hundred milliseconds.
2. Generation. A small model rewrites the matched card into one sentence the
   rep can say right now, notes any discovery facts the prospect gave away,
   and logs the next step once she agrees to one.

The card is never blocked on the model: if generation is slow or fails, the rep
still has the card. Everything here is plain Python with an injected OpenAI
client, so it runs and tests without a LiveKit room.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

EMBED_MODEL = "text-embedding-3-small"
LINE_MODEL = "gpt-4o-mini"
# Cosine floor for text-embedding-3-small against the trigger phrases below.
# Measured on 2026-10-09 with measure_floor.py: 14 prospect lines scored 0.48
# to 0.85 against their own card, and greetings or small talk 0.23 to 0.32.
# A miss is cheap: the rep still gets a line and the next discovery question.
FLOOR = 0.42
MAX_CUES = 3
MAX_TEXT = 220

SELLER = "Ledgerline"
PROSPECT = "Dana Okafor"
COMPANY = "Kestrel Freight"


@dataclass(frozen=True)
class Card:
    id: str
    kind: str  # "objection" | "competitor" | "signal"
    title: str
    say: str
    ask: str
    covers: str  # the discovery item `ask` moves forward
    note: str = ""
    triggers: tuple[str, ...] = ()

    def public(self) -> dict:
        data = {"id": self.id, "kind": self.kind, "title": self.title, "say": self.say}
        if self.note:
            data["note"] = self.note
        return data


# A fictional playbook for a fictional product. Every figure is made up.
PLAYBOOK: tuple[Card, ...] = (
    Card(
        "price",
        "objection",
        "Price",
        "Most teams fund it from what they stop losing: duplicate payments and late fees "
        "usually cover the first year.",
        "What did duplicate or late payments cost you last year?",
        "impact",
        triggers=(
            "that's too expensive for us",
            "the last quote we got for something like this was way more than we can spend",
            "we don't have budget for a new tool",
            "what does this cost",
            "I can't justify that spend to my CFO",
        ),
    ),
    Card(
        "timing",
        "objection",
        "Timing",
        "Fair. Teams usually start right after a close, so the next one runs on Ledgerline. "
        "Pick that date together.",
        "When would this need to be live to matter?",
        "timeline",
        triggers=(
            "this isn't a priority right now",
            "maybe after year-end close",
            "let's revisit next quarter",
            "we're too busy to take this on right now",
            "the timing is bad for us",
        ),
    ),
    Card(
        "status-quo",
        "objection",
        "Status quo",
        "Manual works until volume grows. Ask what happens to close if invoice volume doubles.",
        "How many invoices a month, and how many people touch each one?",
        "process",
        triggers=(
            "our team handles it fine manually",
            "spreadsheets work well enough for us",
            "we've always done it this way",
            "my clerks are fast enough",
            "I'm not sure we need a tool for this",
        ),
    ),
    Card(
        "integration",
        "objection",
        "NetSuite and IT",
        "Native NetSuite connector with two-way sync and no IT build. Most teams go live in "
        "about three weeks.",
        "Who owns NetSuite admin on your side?",
        "process",
        note="Fictional: median go-live 21 days.",
        triggers=(
            "does it work with NetSuite",
            "IT has no bandwidth for another integration",
            "how long does implementation take",
            "we can't take on another integration project",
            "our ERP is heavily customized",
        ),
    ),
    Card(
        "security",
        "objection",
        "Security review",
        "SOC 2 Type II, data held in Canada or the US, and the security pack goes to your "
        "CISO before they ask.",
        "Who runs vendor security reviews, and how long do they take?",
        "decision",
        triggers=(
            "is it SOC 2 compliant",
            "where is our data stored",
            "our CISO will have to review this",
            "I'm worried about vendor bank details being exposed",
            "security is going to be a concern",
        ),
    ),
    Card(
        "adoption",
        "objection",
        "Team adoption",
        "Approvers approve from email or Slack, so only AP logs in. Training is one "
        "45 minute session.",
        "Who in AP would champion this?",
        "process",
        triggers=(
            "my team won't use another tool",
            "training always takes forever",
            "people here hate change",
            "the team is already stretched thin",
            "approvers never log into these systems",
        ),
    ),
    Card(
        "brush-off",
        "objection",
        "Brush-off",
        "Happy to send it. So it's the right thing: is close time, duplicates or approvals "
        "the bigger problem?",
        "Where does an invoice get stuck between arriving and being paid?",
        "pain",
        triggers=(
            "just send me some information",
            "can you email me a deck",
            "I'll look at it later",
            "let me think about it and get back to you",
        ),
    ),
    Card(
        "quillpay",
        "competitor",
        "Quillpay",
        "Quillpay is strong on payments. Ask how they three-way match freight invoices "
        "against NetSuite receipts.",
        "Did Quillpay show you a three-way match on a partial shipment?",
        "process",
        note="They win: card payments, rebates. We win: PO matching, multi-entity.",
        triggers=(
            "we're already evaluating Quillpay",
            "Quillpay gave us a demo last week",
            "how are you different from Quillpay",
            "Quillpay quoted us less",
            "we're leaning toward another vendor",
        ),
    ),
    Card(
        "tallyforge",
        "competitor",
        "Tallyforge",
        "Tallyforge fits single-entity teams. With several entities, ask how they route "
        "approvals across them.",
        "How many legal entities do you pay invoices from?",
        "process",
        note="They win: price for one entity. We win: multi-entity approvals.",
        triggers=(
            "our sister company uses Tallyforge",
            "we looked at Tallyforge last year",
            "isn't Tallyforge the standard for this",
        ),
    ),
    Card(
        "duplicates",
        "signal",
        "Duplicate payments",
        "That's the opening. Ledgerline flags duplicates before payment, across vendor name "
        "variants and invoice-number typos.",
        "How did you find out about the duplicates?",
        "impact",
        triggers=(
            "we paid the same invoice twice",
            "we had duplicate payments last quarter",
            "a carrier got paid twice",
            "we keep overpaying vendors",
        ),
    ),
    Card(
        "close",
        "signal",
        "Slow close",
        "Live accruals from unapproved invoices usually take two to three days off close.",
        "Which days of close go to chasing approvals?",
        "impact",
        triggers=(
            "month-end close takes way too long",
            "our close takes nine days",
            "we spend month end chasing approvals",
            "accruals are a mess",
        ),
    ),
    Card(
        "buying-group",
        "signal",
        "Buying group",
        "Map it now: offer a technical demo for the controller and a one-page business case "
        "for the CFO.",
        "What would your CFO need to see to say yes?",
        "decision",
        triggers=(
            "the CFO signs off on anything like this",
            "I'd need to bring in my controller",
            "this would have to go through procurement",
            "it's not only my decision",
        ),
    ),
)
CARDS = {card.id: card for card in PLAYBOOK}

# Discovery the rep should finish before asking for a next step, in order.
DISCOVERY: tuple[tuple[str, str, str], ...] = (
    ("pain", "Pain", "Where does an invoice get stuck between arriving and being paid?"),
    ("impact", "Impact", "What does that cost you, in close days or overpayments?"),
    ("process", "Process", "Walk me through approvals today. Who touches each invoice?"),
    ("decision", "Decision", "Besides you, who signs off on a tool like this?"),
    ("timeline", "Timeline", "When would this need to be live to matter?"),
    ("budget", "Budget", "Is there budget set aside for finance tooling this year?"),
)
DISCOVERY_KEYS = tuple(key for key, _, _ in DISCOVERY)
CLOSE_ASK = "Ask for the next step: a technical demo with her controller."
BOOKED_ASK = "Next step booked. Recap her pain in one line and confirm who joins."

LINE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "cue",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["say", *DISCOVERY_KEYS, "next_step"],
            "properties": {
                "say": {"type": "string"},
                "next_step": {"type": "string"},
                **{key: {"type": "string"} for key in DISCOVERY_KEYS},
            },
        },
    },
}

LINE_PROMPT = f"""You are a silent sales copilot. A rep from {SELLER}, accounts payable \
automation software, is on a discovery call with {PROSPECT}, VP Finance at {COMPANY}. \
You never speak on the call; you write to the rep's screen.

Return JSON:
- say: the one sentence, at most 22 words, the rep should say out loud next, in reply \
to the prospect's latest line. Use the playbook card's angle: for an objection, answer \
it; for a competitor, ask its pointed question; for a buying signal, dig into it. \
Never invent a number, customer or feature the card does not give you. Do not open \
with "I understand" or "Great". No quotes.
- discovery notes, at most 6 words each, only for what the prospect has actually \
stated in the transcript, else an empty string. Never guess:
  pain: what goes wrong today.
  impact: what it costs, in money or time.
  process: how invoices and approvals work today, or volumes.
  decision: who signs off or must review.
  timeline: when it must be live, or a deadline. A meeting date is not a timeline.
  budget: money set aside or a spending limit, as an amount.
- next_step: when the prospect's latest line agrees to a specific next step (a demo, \
a meeting), that step and its day in at most 8 words. Otherwise an empty string."""


def clip(text: str, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def ms_since(start: float, clock: Callable[[], float]) -> int:
    return max(0, int(round((clock() - start) * 1000)))


# The playbook is the same for every call, so each worker embeds it once and
# reuses the read-only array. Two first calls may both build it; either wins.
_index: dict | None = None


async def playbook_index(client) -> dict | None:
    global _index
    if _index is not None:
        return _index
    rows = [(card.id, text) for card in PLAYBOOK for text in (card.title, *card.triggers)]
    try:
        resp = await client.embeddings.create(model=EMBED_MODEL, input=[t for _, t in rows])
    except Exception:
        logger.warning("Playbook index unavailable; cues fall back to discovery questions")
        return None
    vectors = np.asarray([item.embedding for item in resp.data], dtype=np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    _index = {"ids": [card_id for card_id, _ in rows], "vectors": vectors}
    return _index


def best_card(index: dict, vector: list[float]) -> tuple[Card | None, float]:
    query = np.asarray(vector, dtype=np.float32)
    query /= np.linalg.norm(query) or 1.0
    scores = index["vectors"] @ query
    # Score a card by its closest trigger phrase.
    best: dict[str, float] = {}
    for card_id, score in zip(index["ids"], scores.tolist()):
        best[card_id] = max(score, best.get(card_id, -1.0))
    card_id, score = max(best.items(), key=lambda item: item[1])
    return (CARDS[card_id] if score >= FLOOR else None), round(float(score), 3)


@dataclass
class Cue:
    n: int
    heard: str
    card: Card | None = None
    score: float | None = None
    retrieval_ms: int | None = None
    line: str | None = None
    line_ms: int | None = None
    ask: str | None = None
    done: bool = False

    def public(self) -> dict:
        return {
            "n": self.n,
            "heard": self.heard,
            "card": self.card.public() if self.card else None,
            "score": self.score,
            "retrieval_ms": self.retrieval_ms,
            "line": self.line,
            "line_ms": self.line_ms,
            "ask": self.ask,
            "done": self.done,
        }


@dataclass
class Copilot:
    """One call's copilot. `publish` gets the full snapshot after every change.

    `meter(model, input_tokens, output_tokens)` is told about every billed
    request, so a host can attribute the copilot's spend to the call.
    `max_lines` bounds generation; retrieval keeps working after it runs out.
    """

    client: object
    publish: Callable[[dict], None]
    meter: Callable[[str, int, int], None] = lambda *_: None
    max_lines: int = 14
    max_lookups: int = 24
    clock: Callable[[], float] = time.perf_counter
    cues: list[Cue] = field(default_factory=list)
    found: dict[str, str] = field(default_factory=lambda: dict.fromkeys(DISCOVERY_KEYS, ""))
    words: dict[str, int] = field(default_factory=lambda: {"rep": 0, "prospect": 0})
    transcript: list[tuple[str, str]] = field(default_factory=list)
    next_step: str | None = None
    lines: int = 0
    lookups: int = 0
    card_times: list[int] = field(default_factory=list)
    line_times: list[int] = field(default_factory=list)
    _tasks: set[asyncio.Task] = field(default_factory=set)
    _closed: bool = False

    # Hearing -------------------------------------------------------------

    def heard_rep(self, text: str) -> None:
        text = clip(text, 400)
        if not text or self._closed:
            return
        self.words["rep"] += len(text.split())
        self.transcript.append(("Rep", text))
        self.emit()

    def heard_prospect(self, text: str) -> Cue | None:
        """Start a cue for a finished prospect line. The clock starts now."""
        start = self.clock()
        text = clip(text, 400)
        if not text or self._closed:
            return None
        self.words["prospect"] += len(text.split())
        self.transcript.append(("Prospect", text))
        cue = Cue(n=(self.cues[0].n + 1 if self.cues else 1), heard=clip(text))
        self.cues = [cue, *self.cues][:MAX_CUES]
        task = asyncio.create_task(self._cue(cue, text, start))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return cue

    # Working -------------------------------------------------------------

    async def _cue(self, cue: Cue, text: str, start: float) -> None:
        try:
            await self._retrieve(cue, text, start)
            cue.ask = self.next_question(cue.card)
            self.emit()
            await self._write(cue, start)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Copilot cue failed", exc_info=True)
        finally:
            cue.ask = self.next_question(cue.card)
            cue.done = True
            self.emit()

    async def _retrieve(self, cue: Cue, text: str, start: float) -> None:
        if self.lookups >= self.max_lookups:
            return
        self.lookups += 1
        index = await playbook_index(self.client)
        if index is None:
            return
        resp = await asyncio.wait_for(
            self.client.embeddings.create(model=EMBED_MODEL, input=[text]), timeout=3
        )
        self.meter(EMBED_MODEL, getattr(resp.usage, "total_tokens", 0), 0)
        cue.card, cue.score = best_card(index, resp.data[0].embedding)
        cue.retrieval_ms = ms_since(start, self.clock)
        self.card_times.append(cue.retrieval_ms)

    async def _write(self, cue: Cue, start: float) -> None:
        if self.lines >= self.max_lines:
            return
        self.lines += 1
        card = cue.card
        context = "\n".join(f"{who}: {line}" for who, line in self.transcript[-6:])
        playbook = (
            f"Playbook card ({card.kind}, {card.title}): {card.say}"
            + (f" {card.note}" if card.note else "")
            if card
            else "No playbook card matched. Acknowledge and move discovery forward."
        )
        resp = await asyncio.wait_for(
            self.client.chat.completions.create(
                model=LINE_MODEL,
                messages=[
                    {"role": "system", "content": LINE_PROMPT},
                    {
                        "role": "user",
                        "content": f"Transcript:\n{context}\n\n{playbook}\n\n"
                        f"Latest prospect line: {cue.heard}",
                    },
                ],
                response_format=LINE_SCHEMA,
                max_completion_tokens=160,
                temperature=0.3,
            ),
            timeout=4,
        )
        usage = resp.usage
        self.meter(
            LINE_MODEL,
            getattr(usage, "prompt_tokens", 0),
            getattr(usage, "completion_tokens", 0),
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        say = clip(data.get("say", ""), 200)
        cue.line = say or None
        if say:
            cue.line_ms = ms_since(start, self.clock)
            self.line_times.append(cue.line_ms)
        for key in DISCOVERY_KEYS:
            note = clip(data.get(key, ""), 60)
            if note:
                self.found[key] = note
        # Logged like a CRM field: the first agreed step sticks.
        step = clip(data.get("next_step", ""), 80)
        if step and not self.next_step:
            self.next_step = step

    def next_question(self, card: Card | None) -> str:
        if self.next_step:
            return BOOKED_ASK
        if card and not self.found.get(card.covers):
            return card.ask
        for key, _, question in DISCOVERY:
            if not self.found[key]:
                return question
        return CLOSE_ASK

    # Showing -------------------------------------------------------------

    def snapshot(self) -> dict:
        def median(values: list[int]) -> int | None:
            return int(statistics.median(values)) if values else None

        return {
            "cues": [cue.public() for cue in self.cues],
            "found": dict(self.found),
            "words": dict(self.words),
            "next_step": self.next_step,
            "latency": {
                "card": median(self.card_times),
                "line": median(self.line_times),
            },
        }

    def emit(self) -> None:
        if not self._closed:
            self.publish(self.snapshot())

    async def aclose(self) -> None:
        self._closed = True
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
