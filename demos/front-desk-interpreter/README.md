# Front desk interpreter

Two languages, one front desk. Real-time, both directions.

## Run

From this directory, copy `.env.example` to `.env` and fill in the listed credentials.

```sh
cp .env.example .env
uv sync
uv run python agent.py dev
```

Connect a LiveKit client with a microphone. This example uses room participants
and audio tracks. Optional UI events need a compatible client; the voice
conversation runs without website metadata. Provider usage may cost money.

Two speakers can join the same room. Use headphones to avoid echo.
