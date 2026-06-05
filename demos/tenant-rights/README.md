# tenant-rights

Talk to a voice agent about your rights as a US renter. It answers from real
HUD guidance, names the source out loud, and points you to legal help when a
question goes past what a document can answer.

## What it does

- Answers renter questions (deposits, repairs, landlord entry, fair housing,
  eviction basics, general rights) grounded in public-domain HUD material.
- Retrieves the relevant section on every turn and injects it before the model
  answers, so answers come from the documents, not from guessing.
- Names the source out loud and shows it on screen in a Card.
- Refuses and redirects to local legal aid, a tenant lawyer, or a HUD office
  when a question is not covered or needs facts about a specific lease or state.
- Never invents statute numbers, dollar amounts, deadlines, or citations.

Primary stack is full NVIDIA: Riva STT, NIM LLM, Riva TTS, and NIM embeddings,
all under one `NVIDIA_API_KEY`. If you do not set `NVIDIA_API_KEY` but do set
`OPENAI_API_KEY`, the demo falls back to OpenAI for STT, LLM, TTS, and
embeddings, so you can run it with whichever key you have.

This demo carries its own `.env.example` (`NVIDIA_API_KEY` or `OPENAI_API_KEY`,
plus the three LiveKit values) instead of the shared template env. That is a
deliberate, operator-cleared exception to the one-env-example rule.

## Build the index (once)

The agent retrieves from a prebaked index. Build it once before the first run,
with the same provider you will run with:

```sh
cp .env.example .env   # fill NVIDIA_API_KEY, or OPENAI_API_KEY for the fallback
uv sync
uv run --no-project python build_index.py
```

The index records which embedding model produced it, and the agent refuses to
start if your keys select a different one. Rerun `build_index.py` whenever you
change the documents in `data/` or switch providers.

## Run it

1. Fill `.env` with `NVIDIA_API_KEY` and your three LiveKit values.
2. Sync and download the voice models.

   ```sh
   uv run --no-project python agent.py download-files
   ```

3. Start the agent.

   ```sh
   uv run --no-project python agent.py dev
   ```

Then open https://playground.mahimai.ca/demos/tenant-rights, paste your three
LiveKit values, and start talking.

## Test the retrieval core

The grounding and refusal logic is pure and has no LiveKit or network
dependency:

```sh
uv run --no-project python test_rag.py
```

## Recording

Coming after the demo is recorded.
