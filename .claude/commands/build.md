---
description: Scaffold today's demo. Inline spec, GitHub issue reference, or interactive.
argument-hint: <slug>: <stt>, <llm>, <tts>. <hook>   OR   #NN   OR   (empty)
allowed-tools: Task, Read, Glob, Grep, Bash, Write, Edit
---

# /build

Invoke the `demo-builder` subagent with `$ARGUMENTS`.

Accepts three input shapes:

- **Inline spec**: `/build medical-discharge: speechmatics, openai, cartesia. Plain-English discharge instructions, on demand.`
- **GitHub issue ref**: `/build #42` (subagent runs `gh issue view 42` and reads the body)
- **Empty**: `/build` (subagent asks one short follow-up per missing field)

When `demo-builder` finishes, confirm the demo boots locally:

```sh
cd demos/<slug> && uv sync && uv run python agent.py download-files && uv run python agent.py dev
```

Then commit. The pre-commit hook regenerates `catalog.json` from the
demo's `playground.json` (if any). The push and PR are manual.
