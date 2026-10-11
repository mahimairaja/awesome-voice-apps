# Agent simulations

Scenario files for [LiveKit Agent Simulations](https://docs.livekit.io/testing/simulations/),
one per demo, named after the demo folder (`drive-thru-coffee.yaml` for
`demos/drive-thru-coffee`). Each scenario scripts a simulated caller and states
the outcome the judge grades against.

Run one from the Actions tab: **Agent simulations**, pick the demo and `text`
or `audio`. Text mode skips speech and only exercises the LLM and tools. The
workflow needs the `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET`
repository secrets for a LiveKit Cloud project.

Locally, from the demo folder with its `.env` filled in:

```bash
lk agent simulate text --scenarios ../../tests/simulations/drive-thru-coffee.yaml agent.py
```
