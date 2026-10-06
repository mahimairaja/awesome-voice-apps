# Talk to Our Team

An inbound sales conversation using OpenAI GPT-Live and LiveKit Agents. Discover
what a visitor wants to build, check a simulated calendar, and confirm one meeting.
No CRM subscription, calendar account, email, or real booking is involved.

Requires Python 3.11+, uv, LiveKit credentials, and OpenAI GPT-Live access.

```sh
cp .env.example .env
# Set credentials in .env, never in source control.
uv sync
uv run agent.py download-files
uv run agent.py console
# Or register with LiveKit for a browser client:
uv run agent.py dev
```

Dispatch the worker with agent name `talk-to-our-team`. Try asking for a voice
agent for five clinics, request Friday, then change to Monday during the lookup.
After one slot is proposed, say `confirm`. Slots are illustrative weekday/time
labels in America/Toronto, not dated calendar events. No real personal details
are needed. State is isolated per call and disappears when the worker exits.

The voice and backend have separate instructions. Tools execute through Responses
delegation. Background context uses `append_thinking`; standing guidance uses
`append_instructions`. `generate_reply` requests the greeting through commentary.
A two-second lookup delay makes corrections observable; set `SDR_LOOKUP_DELAY=0`
for an immediate lookup. VAD cuts local playback during interruptions.

The example rejects lookup results after a newer finalized caller transcript and
requires a fresh standalone confirmation for a proposed slot. Speech recognition
and model tool selection are probabilistic: offline tests establish the state
machine rules, not real audio accuracy or production readiness. A real calendar
would additionally need persistent operation IDs and provider-side idempotency.

```sh
uv run pytest -q
```

VoiceGateway records voice-session seconds and the delegated backend's token
usage separately in local SQLite. Transcript and audio capture are disabled.
Provider costs are estimates; they exclude LiveKit transport and worker hosting.
The example pins VoiceGateway 0.27.0 for GPT-Live duration and backend pricing.

A synthetic-audio smoke test exercised a Friday-to-Monday correction and confirmed
one Monday meeting through the native LiveKit session and public `attach()` API.
This is development evidence, not a reliability or latency benchmark. Earlier
runs exposed stale lookup retries and delayed final transcripts; the tests cover
those guards. A tool arriving before the final confirmation transcript waits up
to three seconds, then fails closed if confirmation is still absent.
