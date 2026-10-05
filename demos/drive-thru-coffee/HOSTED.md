# Hosted coffee playground

This entrypoint runs the same coffee tools as a simulation at mahimai.ca/playground.
It creates no real orders, charges, or bookings. The original `agent.py` remains
independently runnable.

## Runtime

Install `requirements-hosted.txt` in Python 3.11 and run `python hosted.py start`.
The Docker build context is this demo directory. LiveKit uses one process with threaded jobs and
admission limited to two calls. Local Silero VAD handles turn boundaries; the
large multilingual turn detector is not loaded. Every session creates its own cart.

Server secrets required:

- LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
- DEEPGRAM_API_KEY, OPENAI_API_KEY, CARTESIA_API_KEY
- PLAYGROUND_ORIGIN: approved site or preview HTTPS origin
- PLAYGROUND_WORKER_SECRET: same random secret configured on the site
- VOICEGW_COLLECTOR_URL, VOICEGW_API_KEY: private authenticated collector

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

## Offline verification

Run `python -m unittest discover -s . -p test_hosted.py`. No provider requests are
made. These checks cover admission, attribution, generation limits and deadline cleanup; real audio,
concurrent carts, provider telemetry and container memory were also checked locally
on 2026-10-04. Deployment-specific checks still precede launch.


## Budget evidence

Local Linux Docker measurements (2026-10-04): OpenRTC 0.20.1 with its default
turn-detector models used 1.24 GiB at idle. The threaded LiveKit worker registered
at 146 MiB idle. A later Linux test with two real calls reached 216 MiB for the worker and 100 MiB
for the collector. CPU samples during those calls were 32–52% of one core; this
is burst usage, not a measured monthly average.
OpenRTC's default exceeded the $10 allocation on Railway RAM alone, so this
entrypoint uses the smaller runtime. No extra subscription is needed.

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
summary helper; older releases do not. Run `python -m pytest test_hosted.py`
from this folder for offline dispatch, limit, trivia, and metering tests.
