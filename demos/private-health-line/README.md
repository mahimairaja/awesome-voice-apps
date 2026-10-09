# Private health line

A clinical trial check-in call for a fictional Canadian research hospital where
the language model never hears who is calling.

The line calls a trial participant for their weekly symptom diary. The agent
asks for their participant number, then five questions (missed doses, new
symptoms, severity, ER or hospital visits, new medication), validates each
answer in a tool, and flags possible adverse events for the study coordinator.

Each AI provider gets only what its job needs:

| Stage | Provider | Receives |
|---|---|---|
| Speech to text | Deepgram Nova-3 | the caller's audio |
| Language model | OpenAI gpt-4o-mini | the transcript, with the participant number removed |
| Voice | Cartesia Sonic-3 | only the sentences the agent says |

The agent's `stt_node` replaces any spoken number of three or more digits
("412", "4 1 2", "four one two") with `[participant number]` before the session,
the chat history or the model see the transcript, and keeps the number itself.
A number split across two final transcripts ("four one", then "two seven") is
joined and redacted too: until the number is recorded, a short run of digits at
the end of a transcript is held back in case the next one continues it.
Every request to the language model goes through a counting `httpx` transport
that measures its size and checks its body for the participant number. The
agent publishes those counts, with the form, as a `PrivateLine` event on the
`ui` data topic, so a screen can show that the number reached the model zero
times.

Deepgram still hears the number, because it transcribes the call. If your
rules forbid any outside processor, run speech to text on your own servers; the
redaction step stays the same.

## Run it

```sh
cp .env.example .env   # LiveKit, Deepgram, OpenAI and Cartesia keys
uv sync
uv run python agent.py console
```

Run `uv run python agent.py dev` instead to connect a LiveKit client.

This is a simulation. It gives no medical advice and saves nothing.
