# livekit-base

The single starter scaffold every demo in this cookbook copies from. It runs
a LiveKit voice agent with Deepgram STT, OpenAI LLM, Cartesia TTS, Silero
VAD, and the LiveKit multilingual turn detector.

## What you get

Five files. No more.

- `agent.py`: ~50 lines of voice agent code, ready to customize.
- `pyproject.toml`: uv-managed dependencies.
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

2. Install dependencies with [uv](https://docs.astral.sh/uv/).

   ```sh
   uv sync
   ```

3. Download the Silero VAD weights and the turn detector model.

   ```sh
   uv run python agent.py download-files
   ```

4. Start the agent in dev mode and connect a frontend.

   ```sh
   uv run python agent.py dev
   ```

   Then open one of:

   - [agents-playground.livekit.io](https://agents-playground.livekit.io) (free, hosted, works out of the box)
   - The local [agent-starter-react](https://github.com/livekit-examples/agent-starter-react) frontend (clone, `pnpm dev`)

   You should hear the agent greet you within a few seconds. Speak; it
   responds.

## Use it for a demo

Copy the whole folder into `demos/<slug>/` and customize from
there. The usual edits per demo:

- The `instructions` string on the `Assistant` class.
- One or more `@function_tool` methods.
- Provider swaps (different TTS voice, a different LLM, etc).

Keep net new code under 300 lines. Bigger ideas become template extensions
instead.

## Swap a provider

To use a different STT / LLM / TTS, edit `AgentSession` in `agent.py` and
update `pyproject.toml` to add or remove the matching `livekit-agents`
extra (for example, `[elevenlabs]` for ElevenLabs TTS), then re-run
`uv sync`.
