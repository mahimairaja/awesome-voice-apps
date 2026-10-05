# Water tracker

Logs glasses of water by voice and tracks progress toward a daily goal.

## Run

From this directory, copy `.env.example` to `.env` and fill in the listed credentials.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Run `uv run python agent.py dev` instead to
connect a LiveKit client. Optional UI events need a compatible client; the voice
conversation runs without website metadata. Provider usage may cost money.
