# Interview coach

A short mock interview with two interviewers, run blind. One is a classic speech
pipeline (Deepgram STT, `gpt-4o-mini`, Cartesia TTS). The other is OpenAI GPT-Live,
a full-duplex model that listens while it talks. Each asks one question and one
follow-up; the order is random, and the agent hands off between them mid-call.

Every reply is timed from the end of your speech to the first audio of the
answer. Pipeline turns also report end of turn, transcription, first token and
first audio. The timings, and which interviewer was which, are published as
`ui_event` data packets on the `ui` topic. A client is optional.

## Credentials

`.env.example` lists them: a LiveKit project, OpenAI (with GPT-Live access),
Deepgram and Cartesia.

## Run

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Run `uv run python agent.py dev` instead to
connect a LiveKit client. Provider usage costs money; GPT-Live bills per minute
of session.
