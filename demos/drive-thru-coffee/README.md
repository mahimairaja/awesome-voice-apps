# Drive-thru coffee

Takes a coffee order, modifies items mid-flow, totals the cart.

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

The optional maintained hosted worker is documented in [HOSTED.md](HOSTED.md).
It is not needed to run or contribute a standalone example.
