# Support cost router

The support line for a made-up mobile carrier, built so its model bill does
not grow with every call. Each caller turn is embedded once, and that one
vector does two jobs:

- **Semantic answer cache.** If the turn matches one of the questions that
  dominate support volume (roaming, eSIM, hotspot, autopay), the stored answer
  is spoken with no model call. A general question the cache misses is
  answered by a model and cached for the rest of the call, so asking it again
  is free.
- **Per-turn model routing.** Otherwise the turn goes to the cheapest model
  whose example turns it resembles: small talk to `gpt-4.1-nano`, account
  questions to `gpt-4.1-mini`, disputes and churn risks to `gpt-4.1`. Dispute
  words always escalate, and a dispute stays on the big model until it is
  resolved.

Every turn is priced as it ran and as it would have run on `gpt-4.1`. The
`CostLedger` UI event carries both, the model mix and the reason for each
route. The account on the line is fictional, and so are the credits.

Ask why the bill went up, that the duplicate charge be removed, whether
roaming works in Mexico, and then a general question twice.

## Run

From this directory, copy `.env.example` to `.env` and fill in the LiveKit,
OpenAI, Deepgram and Cartesia keys.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Run `uv run python agent.py dev` instead to
connect a LiveKit client. Optional UI events need a compatible client; the voice
conversation runs without website metadata. Provider usage may cost money.

## Where the patterns live

- `router.py` `route`: cache lookup, routing by example, escalation rules and
  the sticky dispute. `CACHE_HIT` is the similarity line, with the measurements
  behind it.
- `router.py` `TIER_TOOLS`: the tools each tier may call. Only `gpt-4.1` can
  issue a credit, so a routing mistake never moves money.
- `agent.py` `CostRouter.llm_node`: calls the routed model directly, speaks
  cache hits without a model, and caches complete answers to general questions.
- `router.py` `Ledger`: actual cost against the `gpt-4.1` baseline per turn.
  Turns no model ran for are priced from estimated tokens and marked.

Preemptive generation is off for this agent: the router has to see the final
transcript, not a draft.

## Tests

```sh
uv run --with pytest --with pytest-asyncio python -m pytest ../../tests/support-cost-router
```
