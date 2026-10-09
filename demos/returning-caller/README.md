# Returning caller

A private bank's concierge line that remembers you between calls, with your
consent. Mention a trip, a preference or something you are waiting on, and the
agent asks before it keeps anything. Each note is kept with a reason and an
expiry. When you hang up it writes a two-sentence summary. Call again and it
picks up where you left off. Say "forget that" and the note is deleted.

The bank (Corvel Private) is fictional, and nothing real happens on the call.

## Run

From this directory, copy `.env.example` to `.env` and fill in the LiveKit,
Deepgram, OpenAI and Cartesia keys.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Hang up, run it again, and the second call
starts from the file in `.memory/`. Run `uv run python agent.py dev` instead to
connect a LiveKit client; each caller identity gets its own file. The optional
`Recall` UI event needs a compatible client. Provider usage may cost money.

## Where the patterns live

- `memory.py` `add`: nothing is kept without consent on file, the expiry comes
  from the note's kind (preference 30 days, task 7, context 3), and the file is
  capped at eight notes.
- `memory.py` `screen`: account, card and phone numbers, credentials and
  government IDs are never kept, whatever the caller asks.
- `memory.py` `forget`: deleting a note removes its text and keeps a tombstone.
  Withdrawing consent deletes everything.
- `agent.py` `Concierge.load_file`: the file goes into the instructions at the
  start of the call, with note ids so "forget that" can name one.
- `agent.py` `Concierge.write_summary`: one short LLM call after hang-up, only
  with consent, with number runs masked. Only the latest summary is kept.
- `agent.py` `FileStore`: the storage seam. Swap it for your CRM or database.

Unit tests: `uv run --with pytest --with pytest-asyncio pytest ../../tests/returning-caller`.
