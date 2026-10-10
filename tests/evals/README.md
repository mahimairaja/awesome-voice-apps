# Demo evals

Voice evals for the demos hosted on the mahimai.ca playground, built on
[DeepEval](https://deepeval.com/docs/evaluation-voice). Contributors never need
this: it calls the live hosted demos and is run by maintainers.

## How a call is scored

1. `test_demos.py` reserves an eval call on mahimai.ca for each golden in
   `.dataset.json`. Eval calls have their own monthly budget (capped at $3)
   and never spend the visitors' allowance.
2. DeepEval's `ConversationSimulator` joins the LiveKit room with
   `LiveKitConnector` and plays the caller. The caller is scripted: the lines
   are in each golden's `additional_metadata.script`, so there is no LLM in the
   loop and every run says the same thing. A line can read a value off the
   demo's screen, like `{Refill.label.rx|spell}`.
3. The caller speaks with Kokoro on the CPU (`speech.py`). The agent's words
   come from its own LiveKit transcript, with faster-whisper as a local
   fallback. The only paid part is the demo itself.
4. The seven local voice metrics in `metrics.py` score the call audio.
   `TranscriptionAccuracyMetric` is left out because it needs an LLM judge.
   Audio Integrity and Voice Reliability are stored but never fail a call:
   DeepEval counts the normal pauses between words as dropouts, even on clean
   TTS audio (see `INFORMATIONAL` in `report.py`).
5. The transcript, timings and scores are stored on mahimai.ca. The worker
   reports the tools the agent called. The workflow's `judge` job then runs
   Claude Code on the maintainer's Claude subscription (`CLAUDE_CODE_OAUTH_TOKEN`)
   to judge goal, relevance, tool use and policy, following `JUDGE.md`, and the
   scores are shown on the playground.

The goldens are written by hand rather than with `deepeval generate`, which
needs a paid model.

## Run it

```bash
cd tests/evals
mkdir -p .models
for f in kokoro-v1.0.int8.onnx voices-v1.0.bin; do
  curl -fsSL -o ".models/$f" "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f"
done
uv sync

# Offline tests: no network, no secrets.
uv run pytest test_harness.py

# Live run against the hosted demos (needs the site's eval secret).
PLAYGROUND_EVAL_SECRET=... EVAL_DEMOS=coffee uv run deepeval test run test_demos.py
```

`EVAL_DEMOS` picks demo ids; leave it empty to run every golden. `EVAL_SITE`
points at another deployment, such as a preview.

The `Demo evals` workflow runs on the 1st and 15th of each month, after a push
that redeploys the worker, and by hand. It needs the `PLAYGROUND_EVAL_SECRET`
repository secret.

## Add a scenario

Add a golden to `.dataset.json` with a `name`, `scenario`, `expected_outcome`,
a `persona` with `"speaks_first": false`, and `additional_metadata` holding the
`demo` id, the `script` lines and optionally `seconds` and `expected_tools`.
Each line is said after the agent finishes its turn, so write the lines as
answers to what the agent will ask.

Limits: the caller does not talk over the agent yet (interruption scenarios
need duplex mode), and the worker reports tool calls for cascade demos only.
