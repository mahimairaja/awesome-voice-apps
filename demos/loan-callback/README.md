# Loan application callback

A small business loan application by voice, for a fictional lender, that
survives a dropped call. Every answer is saved to a durable checkpoint before
the agent asks the next question. Hang up halfway, call back, and the agent
greets you by name and asks the exact question you were on.

The new call does not replay the old transcript. It starts with a few lines of
state: the answers so far, notes the agent kept, and the next question. The same
state message replaces older turns inside a call, so every request stays under a
prompt token budget however long the application runs.

## Run

From this directory, copy `.env.example` to `.env` and fill in the LiveKit,
Deepgram, OpenAI and Cartesia keys.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Answer a few questions, stop the agent with Ctrl+C, then start it again: it
resumes from `.checkpoints/console.json`. Run `uv run python agent.py dev` to
connect a LiveKit client instead; checkpoints are then keyed by the caller's
participant identity. Optional UI events (`Resume` on topic `ui`) need a
compatible client; the voice conversation runs without them. Provider usage may
cost money. Brands and applicants are fictional.

## Where the patterns live

- `agent.py` `LoanCallback.checkpoint`: write-through after every recorded
  answer, versioned, timed, and awaited before the tool returns.
- `agent.py` `CheckpointStore` and `FileStore`: swap the file for Redis,
  Postgres or D1 without touching the agent.
- `agent.py` `LoanCallback.restore` and `opening`: load before the first word,
  then resume at `application.current`.
- `agent.py` `LoanCallback.compact`, used in `llm_node`: instructions, the
  state message and the last few turns, held under `TOKEN_BUDGET`.
- `application.py` `record`: the `note` argument is the rolling summary. The
  model writes it in the same tool call, so summarising costs no extra request.

Unit tests: `uv run --with pytest --with pytest-asyncio pytest ../../tests/loan-callback`.
