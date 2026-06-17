# tenant-rights

Talk to a voice agent about your rights as a US renter. It answers from real
HUD guidance, names the source out loud, and points you to legal help when a
question goes past what a document can answer.

## What it does

- Answers renter questions (deposits, repairs, landlord entry, fair housing,
  eviction basics, general rights) grounded in public-domain HUD material.
- Retrieves the relevant section on every turn and injects it before the model
  answers, so answers come from the documents, not from guessing.
- Opens each answer by naming the source out loud, and shows the exact
  section and document on screen in a Card.
- Handles greetings and small talk naturally, and when a question is outside
  the documents, says so briefly instead of guessing or reciting a disclaimer.
- Shows a standing list of topics it can answer and a fixed "information, not
  legal advice" notice, so the screen guides the user and the agent never has
  to repeat the disclaimer.
- Never invents statute numbers, dollar amounts, deadlines, or citations.

The whole stack runs on NVIDIA under one `NVIDIA_API_KEY`: Riva STT, NIM LLM,
Riva TTS, and NIM embeddings. The LLM and embeddings reach NIM over its
OpenAI-compatible endpoint, so the `openai` client rides along as NVIDIA's
transport, not as a second provider.

This demo carries its own `.env.example` (`NVIDIA_API_KEY` plus the three
LiveKit values) instead of the shared template env. That is a deliberate,
operator-cleared exception to the one-env-example rule.

## Build the index (once)

The agent retrieves from a prebaked index. Build it once before the first run:

```sh
cp .env.example .env   # fill NVIDIA_API_KEY
uv sync
uv run --no-project python build_index.py
```

The index records which embedding model produced it, and the agent refuses to
start if a different one is selected. Rerun `build_index.py` whenever you change
the documents in `data/`.

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
