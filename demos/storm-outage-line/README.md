# Storm outage line

A power utility's outage line on a simulated phone network. The caller reads
the service address and account number on their screen to log an outage. Then
they ask to move the call onto a landline or a bad cell connection, turn the
telephony fixes on or off, and read the card again. Each reading is scored
against the card, and the agent can play back how the caller's voice reached it.

The phone line runs inside the worker, between the room's microphone track and
the agent, so voice activity detection and speech-to-text both hear it:

- `web`: untouched browser audio.
- `landline`: 8 kHz with mu-law companding (G.711 style).
- `cell`: landline plus bursty packet loss and network jitter.

The fixes are a deeper adaptive jitter buffer, forward error correction and
packet loss concealment on the receive side, plus Deepgram keyterms for the
street on file and longer endpointing on gappy lines. See `phone_line.py`.

## Credentials

LiveKit, Deepgram, OpenAI and Cartesia. Copy `.env.example` to `.env` and fill
in the keys.

## Run

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Run `uv run python agent.py dev` instead to connect a LiveKit client. The
optional `OutageLine` UI events (topic `ui`) carry the card, the line and the
scored readings for a compatible client; the call works without them. Provider
usage may cost money.

## Test

```sh
uv run pytest -q
```

The tests are offline: they run the phone line on generated tones and call the
tools directly.
