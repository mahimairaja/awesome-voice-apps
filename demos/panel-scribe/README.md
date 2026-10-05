# Panel scribe

Labels each interviewer's voice live in a candidate debrief and turns it into an attributed scorecard.

## Run

From this directory, copy `.env.example` to `.env` and fill in the listed credentials.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Connect a LiveKit client with a microphone. This example uses room participants
and audio tracks. Optional UI events need a compatible client; the voice
conversation runs without website metadata. Provider usage may cost money.

Speaker diarization needs more than one voice; use two or three speakers.
