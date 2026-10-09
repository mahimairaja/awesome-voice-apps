# Phone tree router

A support line for Northvale, a fictional telecom, where the caller says what
they need instead of pressing 1. A router scores every queue on each caller
turn, routes when one queue reaches 75%, asks one clarifying question when two
are close, and never asks more than two. The agent passes a one-line summary to
the team so the caller does not repeat themselves.

The agent publishes a `Router` UI event with the queue scores, the decision,
the handoff note, and the fastest possible path through a classic press-1 menu
to the same queue, so a client can race the two.

## Run

From this directory, copy `.env.example` to `.env` and fill in the LiveKit,
Deepgram, OpenAI and Cartesia keys.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Try "I was charged twice this month", then
"my internet is down and the technician never showed up", which needs a
clarifying question. Run `uv run python agent.py dev` instead to connect a
LiveKit client. Provider usage may cost money.

## Where the patterns live

- `agent.py` `PhoneTreeRouter.classify`: one small structured-output call
  that scores every queue and splits the score when two queues fit.
- `routing.py` `decide`: the policy. Route at 75%, otherwise clarify between
  the top two, and route to the best guess after two questions.
- `agent.py` `on_user_turn_completed`: the router runs before the reply and
  adds its decision to the turn, so the model asks or transfers as told.
- `routing.py` `transfer`: the tool sends the caller to the router's queue,
  never to one the model names.
- `routing.py` `menu_path`: how long a perfect caller spends in the press-1
  menu (read at 150 words a minute, each key pressed the moment its line ends).

Unit tests: `uv run --with pytest --with pytest-asyncio pytest ../../tests/phone-tree-router`.
