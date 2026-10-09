# Build your own agent

One voice agent, configured by data. A config sets the business brief, the
Cartesia voice, which tools are on, and which guardrails apply. Push a new
config mid-call and the agent swaps its instructions, tools and voice in place
(`update_instructions`, `update_tools`), without dropping the call.

- The config can arrive with the job: `{"config": {...}}` in the dispatch metadata.
- A client updates it live with the LiveKit RPC method `agent.configure`, sending
  the full config with a higher `version`. Invalid or stale configs are rejected.
- Platform rules (simulation only, no sensitive data, short replies) are always
  appended after the tenant's brief, so a brief cannot switch them off.
- Every tool is simulated. Nothing is booked, sent or saved.

## Run

From this directory, copy `.env.example` to `.env` and fill in the listed credentials.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

`console` runs the default dentist config. Run `uv run python agent.py dev` to
connect a LiveKit client, then call RPC `agent.configure` on the agent with a
payload like:

```json
{"version": 2, "business": "Spoke & Chain Cycles",
 "prompt": "A bike shop in Ottawa. Tune-ups are $89. Open 10 to 6.",
 "voice": "blake", "tools": ["take_message", "check_availability"],
 "guardrails": ["on_topic", "stick_to_brief"]}
```

Provider usage may cost money.
