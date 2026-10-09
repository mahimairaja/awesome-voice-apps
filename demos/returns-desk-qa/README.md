# Returns desk with a QA supervisor

A returns agent for a made-up homeware store, graded live while the call runs.

The agent verifies the order (number plus ZIP code), applies the return policy
and issues a return number. Its tools enforce the policy: they refuse to act on
an unverified order or an option the policy does not allow.

Beside it, `QaSupervisor` runs a second, cheap model after each agent turn. It
ticks compliance items (monitoring disclosed, policy stated, next steps
recapped), scores the customer's sentiment, and flags any off-policy line. A
flag queues a private note the agent reads before its next reply. The grader
never sits between the customer and a reply.

Try it with order `FW4821` (refundable), `FW5530` (store credit only) or
`FW6017` (final sale) and ZIP code `60614`. Push for a refund on the final sale
throw and watch the grader.

## Run

From this directory, copy `.env.example` to `.env` and fill in the keys.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Run `uv run python agent.py dev` instead to
connect a LiveKit client. The `Orders` and `QaBoard` UI events need a compatible
client; the call works without one. Provider usage may cost money.
