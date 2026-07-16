# panel-scribe

Sit a laptop in the middle of a hiring debrief. The agent labels each
interviewer's voice live, keeps a running who-said-what transcript with
talk-time, and on "scribe, recap" turns the discussion into an attributed
scorecard.

## What it does

- Streams the room's audio to pyannoteAI Live, which separates the voices on one
  microphone into Speaker 1, Speaker 2, and so on in real time.
- Keeps a live speaker-labeled transcript and per-speaker talk-time meters, so a
  panel dominated by one voice is visible at a glance.
- Stays silent while the panel talks. Say "scribe, recap" and it writes a
  scorecard: one row per interviewer with strengths, concerns, and a hiring
  lean, plus a one-line consensus, and reads a short summary back.
- Stack: Deepgram Nova-3 (STT), Cerebras Llama 3.3 70B (LLM), Rime Arcana (TTS),
  pyannoteAI Live (diarization).

## Run it

1. Fill `.env` with your keys.

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

Then open https://playground.mahimai.ca/demos/panel-scribe, paste your three
LiveKit values, and start the debrief.

Diarization needs more than one voice to do anything. To see it work solo, play
a sample panel recording into your microphone, or gather two or three people
around one laptop.

## Recording

Coming after the demo is recorded.
