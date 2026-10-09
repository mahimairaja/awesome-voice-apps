# Furnace repair line

An after-hours emergency line for a made-up heating company. The caller gives
the problem, an address, a callback number and two safety answers, pausing the
way people do ("it's 42... uh... Maple"). LiveKit's audio turn detector decides
when they are done. A plain silence timer runs in shadow on the same audio, and
the `TurnTimeline` UI event marks every place it would have cut the caller off.

- `agent.py`: the agent, its ticket tools (phone and address read-back, gas
  safety, dispatch) and the entrypoint.
- `turns.py`: the shadow timeline and a `TurnDetector` subclass that reports
  each end-of-turn probability. It only watches; the session is unchanged.

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

## Turn detector version

`inference.TurnDetector()` picks its version for you: `dev` and `console` with
LiveKit Cloud keys use the full model on LiveKit Inference (a free monthly
allowance), and `start` outside LiveKit Cloud runs `v1-mini` on your CPU. The
mini model adds roughly 250 MB of resident memory to the worker process, so
size the machine for it or set `FurnaceLine.detector_options` to
`{"version": "v1"}`.

## Tests

```sh
uv run --with pytest --with pytest-asyncio python -m pytest ../../tests/furnace-repair
```
