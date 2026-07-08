# claim-intake

Report a car accident claim by voice. The agent collects a first-notice-of-loss
form, validates each field, fills it in live on screen, then reads the claim back
and files it with a reference number.

## What it does

- Collects the eight FNOL fields one at a time, validating as it goes: the policy
  number must be well-formed, the date of loss cannot be in the future.
- Mirrors the claim in a live KeyValue form with a progress meter; every recorded
  field updates the screen.
- Reads the whole claim back and files it, generating a claim number on a summary
  card.
- Stack: AssemblyAI Universal-3 Pro (STT), Gemini 3 Flash (LLM), Inworld (TTS).

## Run it

1. Fill `.env` with your keys (the Inworld key is Base64-encoded).

   ```sh
   cp .env.example .env
   ```

2. Sync the environment.

   ```sh
   uv sync
   ```

3. Start the agent.

   ```sh
   uv run python agent.py dev
   ```

Then open https://playground.mahimai.ca/demos/claim-intake, paste your three
LiveKit values, and report a claim.

## Recording

Coming after the demo is recorded.
