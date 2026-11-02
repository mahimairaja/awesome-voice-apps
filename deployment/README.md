# Hosted playground

Maintainer-owned integration for mahimai.ca. Contributors only need a runnable demo.

The worker runs on LiveKit Cloud in the `voiceproject` project, not Railway.
Deploy it with `deployment/livekit/deploy.sh` (see below). The single
worker (`playground/hosted.py`, an OpenRTC `AgentPool`) accepts a server-approved
reservation for any demo in `CASCADE_AGENTS` or a GPT Live demo in `REALTIME_AGENTS`. Keep the
`playground` dispatch name; mahimai.ca dispatches calls to it.

To host another STT, LLM and TTS demo: add a small adapter in `playground/`
that loads the contributed agent by path (see `hosted_water.py`), mix it with
`HostedGuard` and register it in `CASCADE_AGENTS`, copy its files in the
Dockerfile (the deploy script stages exactly what the Dockerfile copies). The site must list the
same id before it can reserve a call. Provider keys and pricing must already be
supported by `PlaygroundSink`; unknown pricing ends the call. A demo built on other
providers keeps its own code: the adapter swaps the stack (see `hosted_claim.py`,
which stubs the unused plugin imports while loading). Raise `llm_budget` on the
hosted class when a demo needs more than 12 model turns in two minutes. A demo
that hands off to other agents must charge them to the same call caps (see
`hosted_fraud.py`), or a handoff resets the limits.

Every STT, LLM and TTS client a demo sets goes through `HostedGuard.update_options`,
which swaps it for the LiveKit Inference client with the same model, voice and options
(`playground/inference_swap.py`). The worker holds no Deepgram or Cartesia key, so a
handoff agent built outside `update_options` must use Inference clients itself (see
`hosted_fraud.py`). Usage is billed to the LiveKit project and priced as `livekit`.

A demo that wraps its own provider clients overrides `voice_stack()` (see
`HostedOnprem`): the private health line measures and inspects every language
model request through its own OpenAI client, so it keeps that client instead of
the default one, and sets `own_llm` so the swap leaves it alone. It uses the
worker's OpenAI key.

Required environment: the OpenAI key, `PLAYGROUND_ORIGIN`,
and `PLAYGROUND_WORKER_SECRET`, plus the optional Speechmatics and Spatius keys and
an optional VoiceGateway collector (`VOICEGW_COLLECTOR_URL`, `VOICEGW_API_KEY`). LiveKit Cloud supplies the LiveKit
credentials itself. Keep credentials in LiveKit Cloud secrets, never this repository.

## Deploy to LiveKit Cloud

```bash
lk project set-default voiceproject
# First time: secrets as KEY=value lines in a file outside the repository.
deployment/livekit/deploy.sh create ~/secrets/playground.env
git add deployment/livekit/livekit.toml   # so later deploys update the same agent
# Every later deploy, from the commit you want live:
deployment/livekit/deploy.sh
```

The script stages the files the Dockerfile copies into `deployment/livekit/build/`
and stamps the commit for the /status heartbeat. Change secrets with
`lk agent update-secrets`. The site's LiveKit keys must belong to the same project,
or its dispatch never reaches this worker. Inference models in a demo config are
billed to that project.

The site enforces the shared daily allowance, concurrency and monthly reservation
budget. SDR uses GPT Live with GPT-5.6 Luna, a 120-second deadline, 600 output tokens
per backend response, a 16-response guard, and an estimated $0.20 provider-cost
stop. The site reserves at least $0.50 per SDR call for headroom. Metrics can arrive
late; these are application controls, not a provider invoice cap. Unknown pricing
ends the SDR call. No real calendar or CRM writes occur.

Verify the offline hosted tests and the standalone SDR tests before deploying.
Deploy the compatible worker before enabling SDR admission on the site.
