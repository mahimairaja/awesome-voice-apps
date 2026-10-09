# Fair cancellation line

A membership line for Lumora+, a made-up streaming service, that lets you
cancel. The agent may make one fair retention offer matched to your reason,
but a policy engine around the model decides: it hears the cancel request
itself, says you can say cancel at any time before any offer, drops pressure
lines and deals it did not approve before they are spoken, and cancels on its
own when you decline or three turns pass. The confirmation number is read by
code, one character at a time.

The account is generated per call and published as a `Cancel` UI event with
the cancel clock, the offer and a compliance log. Brands and accounts are
fictional, and this is not legal advice.

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
connect a LiveKit client. Optional UI events need a compatible client; the voice
conversation runs without website metadata. Provider usage may cost money.

## Where the patterns live

- `policy.py` `observe_caller`, called from `CancelLine.on_user_turn_completed`:
  code hears "cancel" on every caller turn, runs the clock, and when it expires
  cancels and speaks with `session.say` and `StopResponse`, so the model gets
  no turn.
- `policy.py` `make_offer` and `OFFERS`: one offer per call, chosen from the
  reason. The model cannot name a price.
- `policy.py` `accept_offer`: an offer applies only when the caller's own last
  words are a clear yes, not when the model says they agreed.
- `policy.py` `screen` and `take_script`, used in `CancelLine.tts_node`:
  required words go first, and each model sentence is screened before TTS.

Unit tests: `uv run --with pytest --with pytest-asyncio pytest ../../tests/fair-cancellation`.
