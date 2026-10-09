# Sales copilot

You play a sales rep on a discovery call. The prospect is an AI buyer, Dana
Okafor, VP Finance at a made-up freight company, and she pushes back: price,
timing, security, NetSuite, a competitor she is already talking to. A second
agent on the same call never speaks. It hears both sides and, for every line
Dana says, puts three things on screen:

- the playbook card that matches (objection handler, competitor battle card or
  buying signal), found by embedding search,
- one line you can say right now, written by a small model from that card,
- the next discovery question to ask, plus a running discovery checklist and
  the next step once she agrees to one.

Both the card and the line are timed from the moment Dana stops talking. The
card never waits for the line. All companies, people and figures are made up.

- `agent.py`: the prospect, the entrypoint, and `watch()`, which feeds both
  sides of the conversation to the copilot.
- `copilot.py`: the playbook, retrieval, the line writer, the discovery
  checklist and the `Copilot` UI event. No LiveKit imports; it runs on any
  transcript.
- `measure_floor.py`: checks the retrieval floor against sample prospect lines
  after you change the playbook.

## Run

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Run `uv run python agent.py dev` instead to
connect a LiveKit client; the copilot's panel arrives as `ui_event` data
packets on the `ui` topic (component `Copilot`, the full snapshot each time).
The voice conversation runs without a client. Provider usage may cost money.

## Tests

```sh
uv run --with pytest --with pytest-asyncio python -m pytest ../../tests/sales-copilot
```

`test_copilot.py` is offline. `test_prospect.py` calls OpenAI.
