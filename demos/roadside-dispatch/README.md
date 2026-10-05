# Roadside dispatch

Roadside dispatcher: scores caller audio with Tyto, adapts when the line degrades, and re-confirms details captured over a bad line.

## Run

From this directory, copy `.env.example` to `.env` and fill in the listed credentials.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Connect a LiveKit client with a microphone. This example inspects room audio
tracks and requires a room connection. Optional UI events need a compatible client; the voice
conversation runs without website metadata. Provider usage may cost money.

The audio-health model downloads during worker prewarming. See `.env.example` for its provider requirements.
