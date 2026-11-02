# Demo evals

Voice evals for the demos hosted on the mahimai.ca playground, built on
[DeepEval](https://deepeval.com/docs/evaluation-voice). Contributors never need
this: it calls the live hosted demos and is run by maintainers.

## How a call is scored

1. `test_demos.py` reserves an eval call on mahimai.ca for each golden in
   `.dataset.json`. Eval calls have their own monthly budget (capped at $3)
   and never spend the visitors' allowance.
2. DeepEval's `ConversationSimulator` joins the LiveKit room with
   `AgentConnector` and plays the caller. The caller is scripted: the lines
   are in each golden's `additional_metadata.script`, so there is no LLM in the
   loop and every run says the same thing. A line can read a value off the
   demo's screen, like `{Refill.label.rx|spell}`. Before each line the caller
   waits until the agent has been quiet for 1.5 seconds, so it never talks over
   an agent that paused mid-reply (a tool call, a lookup).
3. The caller speaks with Kokoro on the CPU (`speech.py`). The agent's words
   come from its own LiveKit transcript, with faster-whisper as a local
   fallback. The only paid part is the demo itself.
4. `metrics.py` scores the call locally, with no paid model:
   - DeepEval's seven voice metrics on the call audio. Voice Consistency,
     Turn-Taking Naturalness and Agent Responsiveness can fail a call. Voice
     Naturalness, Speech Intelligibility, Audio Integrity and Voice
     Reliability are stored but never fail one (`INFORMATIONAL` in
     `report.py`): they are signal heuristics that mostly track how loud the
     audio arrives, and count the normal pauses between words as dropouts.
   - Transcription Accuracy: the agent's transcript of the caller against the
     script lines (1 minus word error rate). Did the agent hear the caller?
   - Agent Intelligibility: faster-whisper on the agent's audio against the
     text the agent published. Does the audio say what the agent meant?
   A reply that starts before the caller finishes is overlap, not a 0 ms
   response, so it is left out of latency.
5. The transcript, timings and scores are stored on mahimai.ca. The worker
   reports the tools the agent called. `judge_claude.py` then has Claude
   Sonnet 5.5 judge goal, relevance, tool use and policy through the Claude API
   (about one cent a call), following `JUDGE.md`, and the scores are shown on
   the playground.

The goldens are written by hand rather than with `deepeval generate`, which
needs a paid model.

## Run it

```bash
cd tests/evals
mkdir -p .models
for f in kokoro-v1.0.int8.onnx voices-v1.0.bin; do
  curl -fsSL -o ".models/$f.part" "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/$f" &&
    mv ".models/$f.part" ".models/$f"
done
uv sync

# Offline tests: no network, no secrets.
uv run pytest test_harness.py

# Live run against the hosted demos (needs the site's eval secret).
# EVAL_AUDIO_DIR keeps each call's audio so you can listen to it.
export DEEPEVAL_TELEMETRY_OPT_OUT=YES
PLAYGROUND_EVAL_SECRET=... EVAL_DEMOS=coffee EVAL_AUDIO_DIR=recordings \
  uv run deepeval test run test_demos.py

# Score the saved calls again offline (each folder's turns.json and WAVs).
uv run python rescore.py recordings

# Judge the recorded calls (Claude API key, about one cent a call).
ANTHROPIC_API_KEY=... PLAYGROUND_EVAL_SECRET=... uv run python judge_claude.py
```

`EVAL_DEMOS` picks demo ids; leave it empty to run every golden. `EVAL_SITE`
points at another deployment, such as a preview. `EVAL_RECORDS=calls.json` keeps the scored
calls in that file instead of posting them, so a trial run never reaches the public
page; judge it with `judge_claude.py --dry-run calls.json`.

The `Demo evals` workflow runs on the 1st and 15th of each month, after a push
that redeploys the worker, and by hand. It needs the `PLAYGROUND_EVAL_SECRET`
repository secret. The judge job uses `ANTHROPIC_API_KEY` when that secret is
set; without it, it falls back to Claude Code with `CLAUDE_CODE_OAUTH_TOKEN`
(see `JUDGE_CLI.md`). Set one of the two, or the calls stay unjudged.

## Add a scenario

Add a golden to `.dataset.json` with a `name`, `scenario`, `expected_outcome`,
a `persona` with `"speaks_first": false`, and `additional_metadata` holding the
`demo` id, the `script` lines and optionally `seconds` and `expected_tools`.
Each line is said after the agent finishes its turn, so write the lines as
answers to what the agent will ask.

Limits: the scripted caller never talks over the agent, so interruption
scenarios are not tested yet, and the worker reports tool calls for cascade
demos only.
