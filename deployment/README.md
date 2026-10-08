# Hosted playground

Maintainer-owned integration for mahimai.ca. Contributors only need a runnable demo.

Build from the repository root with `deployment/playground.Dockerfile`. The single
worker accepts server-approved coffee, trivia, water, roadside and SDR reservations. Keep the
existing `mahimai-playground-coffee` dispatch name for compatibility.

To host another STT, LLM and TTS demo: add a small adapter beside `hosted.py`
that loads the contributed agent by path (see `hosted_water.py`), mix it with
`HostedGuard` and register it in `CASCADE_AGENTS`, copy its files in the
Dockerfile, and add its folder to the Railway watch paths. The site must list the
same id before it can reserve a call. Provider keys and pricing must already be
supported by `PlaygroundSink`; unknown pricing ends the call.

Required environment: LiveKit credentials, OpenAI, Deepgram and Cartesia keys,
`PLAYGROUND_ORIGIN`, `PLAYGROUND_WORKER_SECRET`, `VOICEGW_COLLECTOR_URL`, and
`VOICEGW_API_KEY`. The roadside demo also reads `AIC_SDK_LICENSE` (ai-coustics);
without it the dispatcher still runs but its audio-health meter stays off. Keep
credentials in the hosting platform, never this repository.

The site enforces the shared daily allowance, concurrency and monthly reservation
budget. SDR uses GPT Live with GPT-5.6 Luna, a 120-second deadline, 600 output tokens
per backend response, a 16-response guard, and an estimated $0.20 provider-cost
stop. The site reserves at least $0.50 per SDR call for headroom. Metrics can arrive
late; these are application controls, not a provider invoice cap. Unknown pricing
ends the SDR call. No real calendar or CRM writes occur.

Verify the offline hosted tests and the standalone SDR tests before deploying.
Deploy the compatible worker before enabling SDR admission on the site.
