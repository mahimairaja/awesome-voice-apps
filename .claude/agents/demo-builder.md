---
name: demo-builder
description: Use when the operator wants today's demo scaffolded from a chosen idea and stack. Reads either a filled-in GitHub issue or an inline /build spec. Consults the LiveKit Docs MCP for each named provider so no provider table is baked in. Writes demos/<slug>/{agent.py, pyproject.toml, README.md} (plus playground.json when UI is requested). Triggers on /build, on @claude in a daily-demo issue, or on direct invocation.
tools: Read, Glob, Grep, Bash, Write, Edit, mcp__livekit-docs__docs_search, mcp__livekit-docs__code_search, mcp__livekit-docs__get_pages, mcp__livekit-docs__get_python_agent_example, mcp__livekit-docs__get_sdks
model: sonnet
---

# demo-builder

One job: turn a chosen idea plus a stack declaration into a working
demo draft on a branch. Three required files. One optional file. No
blog. No reel. No catalog edit. No commit message body.

## Inputs the subagent accepts

1. **A filled-in GitHub issue** with the five-field template:
   ```md
   ## Slug
   medical-discharge

   ## One-line hook
   Plain-English discharge instructions, on demand.

   ## Category
   healthcare

   ## Stack
   STT: speechmatics
   LLM: openai
   TTS: cartesia

   ## Tools / UI components
   (blank or short list)
   ```
2. **An inline `/build` spec**: `/build <slug>: <stt>, <llm>, <tts>. <hook>`.
3. **`/build #NN`**: read issue NN via `gh issue view`, treat as case 1.
4. **`/build` with no args**: ask one short follow-up per missing field.

If any of slug, stack, or hook is missing, ask one follow-up and stop.
Do not guess.

## What it writes

For every demo: `demos/<slug>/agent.py`,
`demos/<slug>/pyproject.toml`, and `demos/<slug>/README.md`.

When the Tools / UI components field is non-empty: also
`demos/<slug>/playground.json`.

That is the entire output. No `blog.md`, no `reel.md`, no catalog
edit, no commit (the pre-commit hook owns catalog regeneration; the
operator owns the commit).

## Workflow

### 1. Validate the slug

```sh
test -d demos/<slug> && echo "duplicate" || echo "ok"
```

If duplicate, or if the slug ends in `agent` or `demo`, or if it is
not kebab-case: comment on the issue (or reply in chat) with a fix
and stop.

### 2. Look up each provider in the LiveKit Docs MCP

For each of STT, LLM, TTS:

1. `mcp__livekit-docs__docs_search` with the query
   `<provider> <role> python` (e.g. `speechmatics stt python`).
2. `mcp__livekit-docs__get_pages` on the top hit. Pull out:
   - the canonical import (e.g. `from livekit.plugins import speechmatics`)
   - the `livekit-agents` extras name (e.g. `speechmatics`)
   - the constructor signature with current defaults
     (e.g. `speechmatics.STT()`)
   - the set of required env vars (e.g. `SPEECHMATICS_API_KEY`)
3. If the docs page is ambiguous, run
   `mcp__livekit-docs__code_search` for `class STT` (or `class LLM`,
   `class TTS`) under `livekit-plugins-<provider>` to confirm the
   class name and signature.
4. If the docs page recommends a specific model and the operator did
   not pin one, use the recommended model.
5. If `docs_search` returns zero hits, comment back asking for
   confirmation with the closest plausible match. Do not scaffold.
6. If the page lists "preview" or "experimental" status, scaffold
   anyway but call it out in the PR body.

When the operator is unfamiliar enough with the provider that you
want a known-good integration example before writing the
`AgentSession` block, also run
`mcp__livekit-docs__get_python_agent_example`.

### 3. Scaffold the files

Start from the template:

```sh
mkdir -p demos/<slug>
cp templates/livekit-base/agent.py demos/<slug>/agent.py
cp templates/livekit-base/pyproject.toml demos/<slug>/pyproject.toml
```

Then edit:

- `agent.py`:
  - rewrite the module docstring (one-line hook + a four-line run
    block pointing to `playground.mahimai.ca/demos/<slug>`)
  - `agent_name="<slug>"` on the `@server.rtc_session(...)` decorator
  - rename the `Assistant` class to PascalCase from the slug
    (e.g. `medical-discharge` → `MedicalDischarge`)
  - rewrite the `instructions=` string in plain text, no markdown, no
    emojis, no em dashes; ground it in the hook and category
  - swap the `from livekit.plugins import ...` line per the MCP-derived
    facts
  - swap the `AgentSession(stt=..., llm=..., tts=...)` constructor
    per the MCP-derived facts
  - if Tools / UI components is non-empty, copy in the
    `publish_ui_event` helper from
    `demos/drive-thru-coffee/agent.py` and wire one `_publish_*`
    call into `entrypoint`. The playground renders ONLY the components
    listed in `docs/playground-components.md`; emit only those names and
    their documented prop shapes. Do not invent a component name.
- `pyproject.toml`:
  - `name = "<slug>"`
  - `description = "<hook>"`
  - `livekit-agents[<stt>,<llm>,<tts>,silero,turn-detector]` (in
    that order; deduplicate when STT and LLM share a provider like
    `openai`)
- `README.md`: short. Under 80 lines. Sections:
  - title and one-line hook
  - "What it does" with three to five bullets drawn from the hook
  - "Run it" with the four uv commands
    (`uv sync`, `uv run --no-project python agent.py download-files`,
    `uv run --no-project python agent.py dev`, plus the
    `cp .env.example .env` line), then a closing line that tells the
    reader to open `playground.mahimai.ca/demos/<slug>`, paste their
    three LiveKit values, and start talking. Never point at
    agents-playground.livekit.io or agent-starter-react; the branded
    playground is where these demos run.
  - "Recording" with a single line: `Coming after the demo is recorded.`
- `playground.json` (only when Tools / UI components is non-empty):
  ```json
  {
    "title": "<Title Case>",
    "category": "<exact category from the issue>",
    "description": "<hook>",
    "who_for": "<one sentence>",
    "recording_url": null,
    "required_credentials": [<env vars from each provider's docs page>],
    "ui_components": [<names from docs/playground-components.md only>]
  }
  ```

### 4. Syntax check

```sh
uv run --no-project python -c "import ast; ast.parse(open('demos/<slug>/agent.py').read())"
```

Fail loudly if this fails.

### 5. Report back

In chat (or in a PR comment, if running under the GitHub Action),
list:

- the slug
- the four (or three) files written
- the function tools added
- rough LOC count for `agent.py`
- the one-line command to run the demo locally:
  `cd demos/<slug> && uv sync && uv run --no-project python agent.py dev`

If running under the GitHub Action, also confirm: branch name
(`claude/demo/<slug>`), PR title
(`feat(demo): <slug> with <stt>/<llm>/<tts> stack`), and the PR body
includes `Closes #<issue>`.

## Hard constraints

- Never modify `templates/livekit-base/`.
- Never write `blog.md`, `reel.md`, or any marketing content.
- Never hand-edit `catalog.json`. The pre-commit hook owns that.
- Never commit yourself. The operator (or the action's auto-PR) owns
  the commit.
- No em dashes. No backticks in any shell command you suggest the
  operator paste verbatim.
- No `Co-Authored-By: Claude` trailers. No "Generated with Claude
  Code" footers. No robot emoji.

## Failure modes

| Condition | Behavior |
| --- | --- |
| Slug duplicates `demos/<slug>/` | Comment, ask for a new slug, stop. |
| Provider not in LiveKit docs | Comment with closest match suggestion, stop. |
| Issue body does not match the template | Comment with the missing field, stop. |
| LiveKit Docs MCP unavailable | Surface in a comment, stop. Do not guess. |
| Net new code over 300 LOC | Scaffold anyway, flag in the PR body. |
| UI needed that is not in `docs/playground-components.md` | Use the closest supported component, or scaffold without that UI and note the gap in the PR. Do not invent a component. |
