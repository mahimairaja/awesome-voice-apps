# livekit-base

The single starter scaffold every demo in this cookbook copies from. It runs
a LiveKit voice agent with Deepgram STT, OpenAI LLM, Cartesia TTS, Silero
VAD, and the LiveKit multilingual turn detector.

## What you get

The template keeps the shared files demos copy from.

- `agent.py`: ~50 lines of voice agent code, ready to customize.
- `pyproject.toml`: uv-managed runtime dependencies.
- `.python-version`: Python 3.11.
- `.env.example`: the credentials you need to fill.
- `.gitignore`: keeps `.env` and friends out of git.

## Run it

You need a free [LiveKit Cloud](https://cloud.livekit.io/) project (or a
self-hosted LiveKit server), plus API keys for OpenAI, Deepgram, and
Cartesia.

1. Copy `.env.example` to `.env` and fill in your six keys.

   ```sh
   cp .env.example .env
   ```

2. Sync the environment with
   [uv](https://docs.astral.sh/uv/).

   ```sh
   uv sync
   ```

3. Download the Silero VAD weights and the turn detector model.

   ```sh
   uv run --no-project python agent.py download-files
   ```

4. Start the agent in dev mode.

   ```sh
   uv run --no-project python agent.py dev
   ```

   Then open the playground at
   [playground.mahimai.ca](https://playground.mahimai.ca) (a shipped demo
   lives at playground.mahimai.ca/demos/its-slug), paste your three
   LiveKit values when prompted, and start talking. You should hear the
   agent greet you within a few seconds.

## Use it for a demo

Create `demos/<slug>/`, copy `agent.py` and `pyproject.toml` into it,
and customize from there. The usual edits per demo:

- The `instructions` string on the `Assistant` class.
- One or more `@function_tool` methods.
- Provider swaps (different TTS voice, a different LLM, etc).
- A `playground.json` when the demo emits playground UI events. The root
  `catalog.json` is derived from it automatically; never hand-edit the
  catalog.

Keep net new code under 300 lines. Bigger ideas become template extensions
instead.

## Swap a provider

To use a different STT / LLM / TTS, edit `AgentSession` in `agent.py` and
update the `livekit-agents` extras in `pyproject.toml` to add or remove
the matching extra (for example, `[elevenlabs]` for ElevenLabs TTS),
then run `uv sync`.
