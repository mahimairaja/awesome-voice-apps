# Flight rebooking

Rebooks a cancelled flight by voice. The flight search runs as a background
tool, so the agent keeps answering questions about seats, bags and meals while
it searches. The search also sets off a simulated outage of the primary LLM:
a fallback model takes over, then the primary returns after a recovery probe.
Say "knock out the voice" to fail the primary TTS the same way.

The airline, flights and booking are fictional. Outages are injected in
process; nothing calls a provider to make it fail.

## Credentials

Deepgram (STT), OpenAI (primary and fallback LLM, fallback TTS) and Cartesia
(primary TTS), plus LiveKit for `dev` mode.

## Run

From this directory, copy `.env.example` to `.env` and fill in the listed credentials.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Run `uv run python agent.py dev` instead to
connect a LiveKit client. Optional UI events (topic `ui`, component
`Rebooking`) need a compatible client; the voice conversation runs without
them. Provider usage may cost money.
