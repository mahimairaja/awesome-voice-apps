# Pronunciation coach

A speaking coach for a fictional language-learning app, Parlo. Pick English or
French, read the line on screen aloud, and the coach scores every word you
said. It drills only the one or two weakest words, has you re-read the line to
prove the fix, and moves you up or down a level from your first read.

The line and the per-word scores are published as a `Pronounce` UI event. With
the console, the coach says each line aloud before you read it.

## Run

From this directory, copy `.env.example` to `.env` and fill in the LiveKit,
Deepgram, OpenAI and Cartesia keys.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Run `uv run python agent.py dev` instead to
connect a LiveKit client. Provider usage may cost money.

## Where the patterns live

- `agent.py` `PronunciationCoach.stt_node`: keeps every final word with its
  Deepgram confidence until the turn ends.
- `coach.py` `align`: lines the heard words up against the line. A matched word
  scores its confidence, a word heard as something else or not heard scores 0.
- `coach.py` `score_turn` and `brief`: the drill loop runs in code; the LLM gets
  one system line naming the weak words and coaches only those.
- `coach.py` `_close`: the level moves from the first read of each line.
- `agent.py` `PronunciationCoach.__init__`: a longer endpointing delay for
  reading aloud, and no preemptive reply.

Word scores are recognition confidence, a proxy for pronunciation rather than a
phoneme-level assessment. The thresholds at the top of `coach.py` are tuned for
Nova-3; retune them for another model.

Unit tests: `uv run --with pytest --with pytest-asyncio pytest ../../tests/pronunciation-coach`.
