# Hosted playground worker

This worker runs the demos as simulations at mahimai.ca/playground. It creates no
real orders, charges, or bookings. Each demo under `demos/` stays a plain LiveKit
agent that runs on its own; the adapters here load it by path and add the hosted
call limits.

## Runtime

Install `requirements-hosted.txt` in Python 3.11 and run `python playground/hosted.py start`
from the repository root (the Docker build context, see `deployment/playground.Dockerfile`).
OpenRTC's `AgentPool` registers every demo by id and runs each call as a task in one
process, with admission limited to two calls. The job's `agent` metadata picks the demo.
Local Silero VAD handles turn boundaries; `turn_detector=False` keeps the multilingual
turn detector and its inference process out of the worker. Every session creates its own
state.

Server secrets required:

- LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
- DEEPGRAM_API_KEY, OPENAI_API_KEY, CARTESIA_API_KEY
- PLAYGROUND_ORIGIN: approved site or preview HTTPS origin
- PLAYGROUND_WORKER_SECRET: same random secret configured on the site
- VOICEGW_COLLECTOR_URL, VOICEGW_API_KEY: private authenticated collector
- SPATIUS_APP_ID, SPATIUS_API_KEY, SPATIUS_AVATAR_ID: the concierge demo's
  avatar. The site needs the same SPATIUS_APP_ID and SPATIUS_AVATAR_ID (never
  the API key) so the browser can draw the face. Optional SPATIUS_USD_PER_MINUTE
  (default 0.02) sets the per-second avatar charge the worker reports. Without
  the keys, concierge calls end before any inference.

Do not put secrets in this repository or browser storage. Do not run this worker
against a public playground until infrastructure and provider spending have been
reviewed against the $10 monthly allocation.

## Admission and cleanup

The site reserves daily seconds, global capacity and monthly budget atomically.
Only its room-scoped token dispatches `mahimai-playground-coffee`, with agent
metadata `coffee`. The worker claims the reservation once before accepting a job.
A rejected, duplicated or expired claim cannot start provider inference.

The worker uses the server-returned deadline, capped at 120 seconds, and shuts down
without draining queued speech when time expires. It asks the site to delete the
room; if that request fails, it disconnects locally and leaves server capacity
reserved until the site's recovery confirms room removal. Token expiration alone
does not terminate a connected call.

VoiceGateway tracks providers with transcript and state snapshot capture disabled.
This telemetry is separate from the site's conservative admission ledger. Provider
billing, network costs and hosting charges still require reconciliation. A budget
alert is not a guaranteed invoice cap.

## Model switcher

A demo with `switchable = True` (the coffee counter, for now) runs the STT, LLM and TTS
the visitor picked on the demo page. The site validates the pick and signs it into the
agent dispatch as `models: {stt, llm, tts}`; the worker keeps only ids listed in
`model_menu.py` and falls back to the default stack for anything else. Every model on
the menu must be priced by VoiceGateway and billed by a provider the sink accepts
(`test_model_menu.py` checks both). The site lists the same ids in
`src/config/playground-models.ts`.

## Status heartbeat

`hosted.py start` also runs `status.py` on a daemon thread. Once a minute it posts
`{"action": "heartbeat"}` to the site's worker endpoint with the demo ids it has
loaded and one probe per provider key: `GET /v1/models/gpt-4o-mini` on OpenAI,
`GET /v1/projects` on Deepgram and `GET /voices?limit=1` on Cartesia. These are
reads, not inference, so they are not billed. The site turns them into
mahimai.ca/status; no new variables are needed.

## Offline verification

Run `python -m unittest discover -s . -p 'test_*.py'`. No provider requests are
made. These checks cover admission, attribution, generation limits and deadline cleanup; real audio,
concurrent carts, provider telemetry and container memory were also checked locally
on 2026-10-04. Deployment-specific checks still precede launch.


## Budget evidence

Local Linux Docker measurements (2026-10-04): OpenRTC 0.20.1 with its default
turn-detector models used 1.24 GiB at idle. The threaded LiveKit worker registered
at 146 MiB idle. A later Linux test with two real calls reached 216 MiB for the worker and 100 MiB
for the collector. CPU samples during those calls were 32–52% of one core; this
is burst usage, not a measured monthly average.
OpenRTC's default exceeded the $10 allocation on Railway RAM alone, so the worker
ran on a threaded LiveKit server until OpenRTC could skip the turn detector. With
`turn_detector=False` (2026-10-10, local Linux, no connected room) the OpenRTC worker
idles at 230 MiB PSS in one process against 225 MiB for the threaded server.
No extra subscription is needed.

Every call limits LLM generations to 12, completion tokens to 180 per request,
serialized chat context to 16 KB, and synthesized input to 4,000 UTF-8 bytes.
Request and synthesis limits scale down with shorter reservations. Provider
connection retries are disabled. VoiceGateway reports provider totals per call;
late or repeated reports cannot reduce the admission ledger's charge. Missing
telemetry never releases the conservative cost reservation. Pricing estimates
must still be reconciled with actual provider plans and invoices.


## Verification and deployment status

Real synthetic speech updated one cart while a simultaneous silent caller retained
an empty cart. Duplicate calls were rejected. Server deadlines disconnected both
120-second calls within 0.23 seconds, and two Linux 60-second calls within 0.14
seconds. Real GitHub login and browser microphone access were checked separately.
VoiceGateway's cumulative-provider normalization fix is pinned to a commit pending
its PR review. LiveKit session recording is explicitly disabled.

The planned Railway collector uses a private service with persistent SQLite.
No public collector domain is needed. The worker connects outbound to LiveKit and
the authenticated site control endpoint. Keep admission disabled until the site,
worker, collector persistence and monthly budget are configured together.

## Two hosted experiences

The shared hosted entry point accepts `coffee` or `trivia` in its validated
reservation metadata. Both dispatch to the same worker and share the site's
quota, concurrency, duration, and budget limits. The hosted trivia variant
in `trivia.py` asks three fixed questions, grades with a deterministic tool,
and publishes `Trivia` events for the website's score panel. Duplicate answers
do not increment the score, and each call owns its own state.

The worker sends allowlisted cumulative `InsideCall` snapshots to the site's
worker endpoint. The website stores the latest revision and exposes it only to
the GitHub account that owns that reservation. No transcript or operator token
is sent to the browser. Provider timings are not end-to-end latency.

Deploy the site migration `0013_playground_insights.sql` and compatible API
before updating this worker. The pinned VoiceGateway revision includes the
summary helper; older releases do not. Run `python -m unittest test_hosted`
from this folder for offline dispatch, limit, trivia, and metering tests.
