# Tenant rights

Answers US renter questions from public HUD guidance, names the source, and redirects to legal help when a question is out of scope.

## Run

From this directory, copy `.env.example` to `.env` and fill in the listed credentials.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python build_index.py
uv run python agent.py console
```

Use your microphone and speakers. Run `uv run python agent.py dev` instead to
connect a LiveKit client. Optional UI events need a compatible client; the voice
conversation runs without website metadata. Provider usage may cost money.

Build the retrieval index before starting: `uv run python build_index.py`.
The files under `data/` are runtime reference material. This is a demonstration, not legal advice.
