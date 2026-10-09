# Manager approval

A returns agent for a made-up home goods store, Harbor & Pine. A customer
wants $649 back for an espresso machine that broke on day 41. The agent may
refund up to $150 inside 30 days by itself, so it asks a manager:

- It sends the manager a short **whisper brief** (one line plus the facts and
  a recommendation) over LiveKit RPC to a manager console page in the room.
- It **keeps the caller company on hold**: check-ins at 12 and 28 seconds,
  never over the caller, and a 45 second timeout that falls back to the
  store's written policy.
- The console calls back over RPC with **approve, counter (store credit),
  decline or take the call**. The agent resumes on the decision at once.
- "Take the call" is a **warm transfer**: the manager, a second voice, joins
  with the brief and the last turns, and never asks the caller to repeat.

Here the manager is simulated on the same call. In production the manager is
a person, dialled with LiveKit's `WarmTransferTask`; the approval handshake
and the brief stay the same.

Nothing is charged or refunded.

## Run

Copy `.env.example` to `.env` and fill in the LiveKit, OpenAI, Deepgram and
Cartesia keys.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Without a console page in the room, every request goes straight to the policy
manager, so the agent still works from the LiveKit playground. The page's
participant token needs `canPublishData`, because RPC travels on the data
channel.

Offline tests: `uv run python -m unittest test_agent`.

## RPC contract

| Direction | Method | Payload | Reply |
| --- | --- | --- | --- |
| agent to page | `approval.request` | `{"id", "amount", "to", "brief": {"line", "rows"}, "timeout"}` | `{"received": true}` |
| page to agent | `approval.decide` | `{"id", "verdict", "note"?}` | `{"ok": true}` |

`verdict` is `approve`, `counter`, `decline`, `take_call`, or `auto` to let
the policy manager decide. The agent refuses a decision from anyone but the
page, for an unknown request id, or after a decision is already made.

The agent also publishes the whole case on the `ui` topic as a `ui_event` with
`component: "Approval"`.
