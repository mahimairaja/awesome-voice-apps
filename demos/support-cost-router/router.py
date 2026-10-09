"""Per-turn model routing, a semantic answer cache, and the cost ledger.

Pure logic with no LiveKit imports, so it is unit-testable offline. The agent
embeds each caller turn once, and this module uses that one vector twice: to
look the question up in the answer cache, and to pick the cheapest model tier
whose example turns it resembles. Every turn is priced twice, once for what
it actually cost and once for what the same turn costs on the big model, so
the panel can show the gap.
"""

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

BRAND = "Northbeam Mobile"

EMBED_MODEL = "text-embedding-3-small"

# Published USD per 1M tokens: (input, cached input, output). These match the
# voice-prices catalogue the playground meters with (checked 2026-10-09).
PRICES: dict[str, tuple[Decimal, Decimal, Decimal]] = {
    "gpt-4.1-nano": (Decimal("0.10"), Decimal("0.025"), Decimal("0.40")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("0.10"), Decimal("1.60")),
    "gpt-4.1": (Decimal("2.00"), Decimal("0.50"), Decimal("8.00")),
}
EMBED_PRICE = Decimal("0.02")  # per 1M input tokens
MILLION = Decimal(1_000_000)

# The tiers, cheapest first. "cache" answers without any model call.
TIERS = ("cache", "nano", "mini", "large")
MODELS = {"nano": "gpt-4.1-nano", "mini": "gpt-4.1-mini", "large": "gpt-4.1"}
BASELINE = "gpt-4.1"

# Cosine thresholds for text-embedding-3-small, measured on 2026-10-09 with
# twelve held-out rephrasings of cached questions: they scored 0.54 to 0.90 and
# 8 of 12 cleared 0.70. The closest wrong intent was "how do I turn off
# autopay" at 0.74 against the autopay entry, so that answer covers both on and
# off. A wrong hit costs more than a miss (the caller hears the wrong answer,
# a miss only costs a model call), so the line sits high.
CACHE_HIT = 0.70
# Below this no example is close enough to trust: use the middle tier.
ROUTE_FLOOR = 0.30

# The answer cache, seeded with the questions that dominate a carrier's
# support volume. Each entry: an id, the question phrasings, the answer that
# is spoken as is. Answers are generic, never about one account.
FAQ: list[tuple[str, tuple[str, ...], str]] = [
    (
        "roaming",
        (
            "Is roaming free in Mexico?",
            "Can I use my phone in Canada without extra charges?",
            "Does my plan work when I travel to Mexico or Canada?",
        ),
        (
            "Yes. Every Northbeam plan covers talk, text and data in Canada and Mexico at "
            "no extra charge. Anywhere else, a travel day pass is twelve dollars, and only "
            "on days you use your phone."
        ),
    ),
    (
        "day_pass",
        (
            "How much is the international day pass?",
            "What does it cost to use my phone in Europe?",
            "How does the travel pass work overseas?",
            "How much is the travel pass?",
        ),
        (
            "A travel day pass is twelve dollars for each day you use your phone outside "
            "North America. It starts on your first call, text or data use that day, and "
            "you are never charged on days the phone stays quiet."
        ),
    ),
    (
        "esim",
        (
            "How do I set up an eSIM?",
            "Can I activate my phone with an eSIM?",
            "How do I move my number to eSIM?",
        ),
        (
            "Open the Northbeam app, tap Add eSIM, and scan the code it shows. It takes "
            "about two minutes and your physical SIM stops working once the eSIM is on."
        ),
    ),
    (
        "hotspot",
        (
            "How much hotspot data do I get?",
            "Can I tether my laptop to my phone?",
            "Is mobile hotspot included in my plan?",
            "Can I share my phone's data with my laptop?",
        ),
        (
            "Unlimited Plus includes fifty gigabytes of high-speed hotspot data a month. "
            "After that, hotspot still works at a slower speed until your next bill."
        ),
    ),
    (
        "autopay",
        (
            "Do I get a discount for autopay?",
            "How do I turn on automatic payments?",
            "Is there a discount if I pay automatically?",
        ),
        (
            "Autopay takes five dollars off every line, every month. You can turn it on or "
            "off in the app under Billing, then Autopay."
        ),
    ),
    (
        "keep_number",
        (
            "Can I keep my number if I switch to Northbeam?",
            "How do I transfer my number from another carrier?",
            "How long does it take to port my number?",
            "Can I bring my number from another carrier?",
        ),
        (
            "Yes, you can keep your number. Have your old carrier's account number and "
            "transfer PIN ready, and most transfers finish within an hour."
        ),
    ),
    (
        "slow_data",
        (
            "What happens after I hit my data limit?",
            "Why is my data so slow at the end of the month?",
            "Do you throttle data after a certain amount?",
        ),
        (
            "Data is never cut off. On Unlimited Plus, after one hundred gigabytes in a "
            "month your speed may slow when the network is busy, and it resets on your "
            "billing date."
        ),
    ),
    (
        "support_hours",
        (
            "What are your store hours?",
            "When is customer support open?",
            "Can I talk to someone at night?",
            "Are you open on weekends?",
            "What time do stores close?",
        ),
        (
            "Phone support is open around the clock. Stores are open ten to eight on "
            "weekdays and eleven to six on weekends."
        ),
    ),
]

# Example caller turns for each routing class. The cache questions above also
# count as "general" examples.
#   chat:     small talk and acknowledgements, a tiny model is enough
#   account:  questions about this caller's account, need a tool call
#   general:  policy questions the cache missed; the answer is cached after
#   escalate: disputes, cancellations, frustration, multi-part problems
EXAMPLES: dict[str, tuple[str, ...]] = {
    "chat": (
        "Hello?",
        "Thanks, that's all.",
        "Okay, got it.",
        "Yes please.",
        "Sorry, can you say that again?",
        "Hang on a second.",
        "Can you hear me?",
        "Goodbye.",
        "Great, thank you so much.",
        "No, that's it.",
    ),
    "account": (
        "Why is my bill higher this month?",
        "What's on my latest bill?",
        "When is my payment due?",
        "How much do I owe right now?",
        "What plan am I on?",
        "Can you check my account?",
        "What is this charge on my bill?",
        "How much data have I used?",
    ),
    "general": (
        "Do you have 5G in Toronto?",
        "Which phones support Wi-Fi calling?",
        "Can I add a line for my daughter?",
        "Do you sell refurbished phones?",
        "How do I block spam calls?",
    ),
    "escalate": (
        "I was charged twice for the same thing and I want it fixed.",
        "This is the third time I'm calling about this.",
        "I want to cancel my service.",
        "Your company overcharged me and I want a refund.",
        "Let me speak to a manager.",
        "Why should I pay for something I never used?",
        "This bill is wrong and nobody has fixed it.",
        "I'm going to switch carriers if this isn't sorted out.",
        "Can you take that charge off my bill?",
        "Please remove the extra charge.",
    ),
}
CLASS_TIER = {"chat": "nano", "account": "mini", "general": "mini", "escalate": "large"}

# Tools each tier may call. Money moves only on the big model, so a routing
# mistake can cost a better answer but never a wrong credit.
TIER_TOOLS = {
    "nano": (),
    "mini": ("look_up_bill",),
    "large": ("look_up_bill", "credit_duplicate"),
}

# Words that always mean money or a churn risk is on the line. Escalation by
# rule is cheap insurance: the large model only costs more on these turns.
ESCALATE_RE = re.compile(
    r"\b(charged twice|double[- ]charged|duplicate\w*|overcharg\w*|refund\w*|dispute\w*|cancel\w*|"
    r"manager|supervisor|switch(ing)? carriers?|complain\w*|unacceptable|ridiculous|"
    r"lawyer|third time)\b",
    re.IGNORECASE,
)
# Words that make a short turn about the account rather than small talk.
ACCOUNT_RE = re.compile(r"\b(bill|charge|charged|owe|payment|plan|account|credit)\b", re.IGNORECASE)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def index_texts() -> tuple[list[str], list[tuple[str, str]]]:
    """Every phrase to embed once per worker, with (kind, key) labels."""
    texts, labels = [], []
    for key, questions, _ in FAQ:
        for q in questions:
            texts.append(q)
            labels.append(("faq", key))
    for cls, examples in EXAMPLES.items():
        for e in examples:
            texts.append(e)
            labels.append(("class", cls))
    return texts, labels


def faq_answer(key: str) -> str:
    return next(answer for k, _, answer in FAQ if k == key)


@dataclass
class Route:
    tier: str
    reason: str
    cls: str = ""
    score: float = 0.0
    cache_key: str = ""
    # A general question answered by a model is cached for the rest of the call.
    learn: bool = False
    # For a cache hit: the answer spoken as is.
    answer: str = ""
    # The turn's embedding, kept so a learned answer can be cached under it.
    vector: list[float] | None = None

    @property
    def model(self) -> str:
        return MODELS.get(self.tier, "")


def _quote(match: re.Match) -> str:
    return f"'{match.group(0).lower()}'"


def route(
    text: str,
    vector: list[float] | None,
    index: dict,
    learned: list[dict],
    dispute_turns: int = 0,
) -> Route:
    """Pick the cheapest tier that can handle this turn, and say why.

    `index` holds the per-worker vectors from index_texts(); `learned` holds
    answers cached earlier in this call. With no vector (the embedding call
    failed) the router falls back to rules, never to the cache.
    """
    words = len(text.split())
    escalate = ESCALATE_RE.search(text)
    if escalate:
        return Route("large", f"Escalation words: {_quote(escalate)}", "escalate", 1.0)
    if dispute_turns > 0:
        return Route("large", "Billing dispute still open, staying on the big model", "dispute")
    if vector is None:
        if words <= 3 and not ACCOUNT_RE.search(text):
            return Route("nano", f"Short turn ({words} words), router offline", "chat")
        return Route("mini", "Router offline, middle tier by default", "general")

    best_faq, best_faq_key = 0.0, ""
    best_class = {cls: 0.0 for cls in EXAMPLES}
    matrix = np.asarray(index["vectors"], dtype=np.float32)
    query = np.asarray(vector, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
    scores = (matrix @ query) / np.where(norms == 0, 1, norms)
    for score, (kind, key) in zip(scores.tolist(), index["labels"], strict=True):
        if kind == "faq":
            if score > best_faq:
                best_faq, best_faq_key = score, key
            best_class["general"] = max(best_class["general"], score)
        else:
            best_class[key] = max(best_class[key], score)
    best_learned, learned_hit = 0.0, None
    for entry in learned:
        score = cosine(vector, entry["vector"])
        if score > best_learned:
            best_learned, learned_hit = score, entry

    if best_faq >= CACHE_HIT and best_faq >= best_learned:
        return Route(
            "cache",
            f"Cached answer '{best_faq_key}' at {best_faq:.2f}",
            "general",
            best_faq,
            cache_key=best_faq_key,
            answer=faq_answer(best_faq_key),
        )
    if learned_hit and best_learned >= CACHE_HIT:
        return Route(
            "cache",
            f"Answered earlier this call, matched at {best_learned:.2f}",
            "general",
            best_learned,
            cache_key=learned_hit["key"],
            answer=learned_hit["answer"],
        )

    cls = max(best_class, key=best_class.get)
    score = best_class[cls]
    if score < ROUTE_FLOOR:
        return Route(
            "mini", f"No close example ({score:.2f}), middle tier by default", "general", score
        )
    if cls == "chat" and ACCOUNT_RE.search(text):
        cls = "account"
    tier = CLASS_TIER[cls]
    reasons = {
        "chat": f"Small talk, closest example at {score:.2f}",
        "account": f"Account question, needs a lookup ({score:.2f})",
        "general": f"Policy question the cache missed ({score:.2f}), caching the answer",
        "escalate": f"Sounds like a dispute ({score:.2f})",
    }
    return Route(tier, reasons[cls], cls, score, learn=cls == "general", vector=vector)


def llm_cost(model: str, prompt: int, completion: int, cached: int = 0) -> Decimal:
    rate_in, rate_cached, rate_out = PRICES[model]
    cached = max(0, min(cached, prompt))
    return ((prompt - cached) * rate_in + cached * rate_cached + completion * rate_out) / MILLION


def embed_cost(tokens: int) -> Decimal:
    return max(0, tokens) * EMBED_PRICE / MILLION


def estimate_tokens(text: str) -> int:
    """About four characters a token for English; used only where no model ran."""
    return max(1, math.ceil(len(text) / 4))


@dataclass
class Turn:
    n: int
    said: str
    tier: str
    model: str
    reason: str
    prompt: int = 0
    completion: int = 0
    cached: int = 0
    actual: Decimal = Decimal(0)
    baseline: Decimal = Decimal(0)
    router: Decimal = Decimal(0)
    first_token_ms: int | None = None
    router_ms: int | None = None
    estimated: bool = False
    tools: list[str] = field(default_factory=list)


class Ledger:
    """What this call cost against the same call on the big model, per turn."""

    def __init__(self) -> None:
        self.turns: list[Turn] = []

    def open(self, said: str, r: Route, router_tokens: int = 0) -> Turn:
        turn = Turn(len(self.turns) + 1, said[:160], r.tier, r.model, r.reason)
        turn.router = embed_cost(router_tokens)
        self.turns.append(turn)
        return turn

    @staticmethod
    def add_usage(
        turn: Turn, model: str, prompt: int, completion: int, cached: int, estimated: bool = False
    ) -> None:
        """One model request inside a turn; a tool call makes a turn two requests."""
        turn.prompt += prompt
        turn.completion += completion
        turn.cached += cached
        turn.actual += llm_cost(model, prompt, completion, cached)
        turn.baseline += llm_cost(BASELINE, prompt, completion, cached)
        turn.estimated = turn.estimated or estimated

    @staticmethod
    def add_skipped(turn: Turn, context_text: str, answer: str) -> None:
        """A turn no model ran for: price what the big model would have read and said."""
        prompt, completion = estimate_tokens(context_text), estimate_tokens(answer)
        turn.prompt += prompt
        turn.completion += completion
        turn.baseline += llm_cost(BASELINE, prompt, completion)
        turn.estimated = True

    def totals(self) -> dict:
        actual = sum((t.actual + t.router for t in self.turns), Decimal(0))
        baseline = sum((t.baseline for t in self.turns), Decimal(0))
        mix = {tier: sum(1 for t in self.turns if t.tier == tier) for tier in TIERS}
        saved = (1 - actual / baseline) if baseline > 0 else Decimal(0)
        return {"actual": actual, "baseline": baseline, "saved": saved, "mix": mix}


# The panel projects this call's mix onto a support line of this size.
CALLS_PER_MONTH = 1_000_000


def usd(value: Decimal, places: int = 6) -> float:
    return float(round(value, places))


def snapshot(ledger: Ledger, learned: list[dict]) -> dict:
    """The JSON the panel draws. Numbers only, plus the caller's own words."""
    totals = ledger.totals()
    return {
        "brand": BRAND,
        "baselineModel": BASELINE,
        "actualUsd": usd(totals["actual"]),
        "baselineUsd": usd(totals["baseline"]),
        "savedPct": round(float(totals["saved"]) * 100, 1),
        "callsPerMonth": CALLS_PER_MONTH,
        "monthlyActualUsd": usd(totals["actual"] * CALLS_PER_MONTH, 0),
        "monthlyBaselineUsd": usd(totals["baseline"] * CALLS_PER_MONTH, 0),
        "mix": totals["mix"],
        "learned": len(learned),
        "turns": [
            {
                "n": t.n,
                "said": t.said,
                "tier": t.tier,
                "model": t.model,
                "reason": t.reason,
                "promptTokens": t.prompt,
                "completionTokens": t.completion,
                "cachedTokens": t.cached,
                "actualUsd": usd(t.actual + t.router, 7),
                "baselineUsd": usd(t.baseline, 7),
                "firstTokenMs": t.first_token_ms,
                "routerMs": t.router_ms,
                "estimated": t.estimated,
                "tools": t.tools,
            }
            for t in ledger.turns[-8:]
        ],
    }


# The account the caller asks about: made up, the same on every call.
ACCOUNT = {
    "holder": "Alex Morgan",
    "plan": "Unlimited Plus",
    "bill_date": "October 3",
    "due_date": "October 24",
    "lines": [
        {"item": "Unlimited Plus plan", "usd": 65.00},
        {"item": "Autopay discount", "usd": -5.00},
        {"item": "Travel day pass, London, September 14", "usd": 12.00},
        {"item": "Travel day pass, London, September 15", "usd": 12.00},
        {"item": "Device protection", "usd": 9.00},
        {"item": "Device protection", "usd": 9.00},
    ],
    "data_used_gb": 38.2,
    "previous_total_usd": 69.00,
    "new_since_last_bill": [
        "Travel day pass, London, September 14",
        "Travel day pass, London, September 15",
        "a second Device protection line",
    ],
}


# Once a turn escalates, the next turns stay on the big model until the dispute
# is resolved or this many turns pass without another escalation. Dropping to
# a small model mid-dispute is where cheap routing loses customers.
STICKY_TURNS = 2


def initial_state() -> dict:
    return {"credited": [], "dispute_turns": 0}


def track_dispute(state: dict, r: Route) -> None:
    if r.cls == "escalate":
        state["dispute_turns"] = STICKY_TURNS
    elif r.cls == "dispute":
        state["dispute_turns"] -= 1


def bill_summary() -> str:
    lines = "; ".join(f"{line['item']} ${line['usd']:.2f}" for line in ACCOUNT["lines"])
    total = sum(line["usd"] for line in ACCOUNT["lines"])
    return (
        f"{ACCOUNT['holder']}, {ACCOUNT['plan']}. Bill dated {ACCOUNT['bill_date']}, due "
        f"{ACCOUNT['due_date']}, total ${total:.2f} (last month ${ACCOUNT['previous_total_usd']:.2f}). "
        f"Lines: {lines}. New since last bill: {'; '.join(ACCOUNT['new_since_last_bill'])}. "
        f"Data used this cycle: {ACCOUNT['data_used_gb']} GB."
    )


def credit(state: dict, item: str) -> str:
    """Credit a line only if it really is billed twice. Simulated; idempotent."""
    wanted = item.strip().lower()
    matches = [line for line in ACCOUNT["lines"] if wanted and wanted in line["item"].lower()]
    if not matches:
        return "rejected: no line on the bill matches that item"
    name = matches[0]["item"]
    if name in state["credited"]:
        state["dispute_turns"] = 0
        return f"already credited: {name}, ${matches[0]['usd']:.2f}"
    # Two travel passes on different days are two valid charges; only an
    # identical line billed twice is a duplicate.
    if sum(1 for line in ACCOUNT["lines"] if line["item"] == name) < 2:
        return f"rejected: {name} appears once and is a valid charge"
    state["credited"].append(name)
    state["dispute_turns"] = 0
    return f"credited: {name}, ${matches[0]['usd']:.2f}, shows on the next bill (simulated)"
