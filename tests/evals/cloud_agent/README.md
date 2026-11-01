# Eval agent on LiveKit Cloud

The scripted caller can talk to a copy of a demo deployed to LiveKit Cloud
instead of the hosted playground worker. The copy runs the demo's own Agent
class and swaps only the STT, LLM and TTS clients for LiveKit Inference, so
it needs no provider keys. LiveKit Cloud injects `LIVEKIT_URL`,
`LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` into the agent.

Today it serves the Coffee counter (`demos/drive-thru-coffee`) as the agent
`eval-coffee`.

## Run it on your machine

A worker on your own machine joins the LiveKit Cloud project like a deployed
one, but takes no agent deployment slot, and still uses LiveKit Inference. Put
the project's `LIVEKIT_URL`, `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` in an
env file outside the repo, then:

```sh
./local.sh ~/path/to/.env.local          # start the worker, run the Coffee evals, stop
./local.sh ~/path/to/.env.local worker   # worker only, for manual calls
```

Results land in `tests/evals/recordings/` (`calls.json` and the audio).
Score them from `tests/evals`:

```sh
cd ..   # tests/evals
uv run python judge_claude.py --dry-run recordings/calls.json
```

## Deploy

```sh
lk cloud auth                         # once
lk project set-default voiceproject   # the eval project
./deploy.sh                           # first time: lk agent create
./deploy.sh update                    # after a demo change: lk agent deploy
```

`stage.sh` (used by both scripts) stages the demo's `agent.py` (as `demo.py`), `pyproject.toml` and
`uv.lock` next to `agent.py` and the `Dockerfile` in `build/`, then runs the
CLI there. The first deploy writes `livekit.toml`; commit it.

`EVAL_STT`, `EVAL_LLM` and `EVAL_TTS` override the models
(`lk agent update-secrets --secrets "EVAL_LLM=..."`).

## Run the evals against it

Add the project's URL, key and secret as the repository secrets
`EVAL_LIVEKIT_URL`, `EVAL_LIVEKIT_API_KEY` and `EVAL_LIVEKIT_API_SECRET`, then
start the `Demo evals` workflow with target `cloud` and demos `coffee`. Locally,
from `tests/evals`:

```sh
EVAL_TARGET=cloud EVAL_DEMOS=coffee LIVEKIT_URL=... LIVEKIT_API_KEY=... \
  LIVEKIT_API_SECRET=... uv run deepeval test run test_demos.py
```

Cloud calls skip the site's budget and are written to `calls.json`
(`EVAL_RECORDS`), which `judge_claude.py --dry-run calls.json` can score.
They carry no tool events (`tools` is empty), so a dry run leaves out the
tool-use score even though the Coffee agent calls its order tools. The agent
serves only Coffee: other demos have no `eval-<demo>` agent to answer.
