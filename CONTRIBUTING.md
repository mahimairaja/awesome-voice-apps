# Contributing

This cookbook is one operator's working log of voice AI demos.
External contributions are welcome but not the primary loop. If you
want to add a demo or fix something, read this once and you have all
the context you need.

For the AI-collaborator version of these conventions, see
[AGENTS.md](AGENTS.md). [CLAUDE.md](CLAUDE.md) is a one-line import of
the same file so Claude Code auto-loads it.

## One-time setup

```sh
make hooks
```

Points git at `.githooks/`. The pre-commit hook regenerates
`catalog.json` from each demo's `playground.json` when a `demos/`
file is staged. The hook is the only thing keeping the root catalog
in sync; without it, `catalog.json` drifts.

If you also want the daily-issue and `@claude` GitHub flows wired up
(see "The daily loop" below), the operator one-time setup is:

1. Install the Claude Code GitHub App on this repo.
2. Add `CLAUDE_CODE_OAUTH_TOKEN` as a repo secret. Generate it locally
   with `claude setup-token` (needs a Claude Pro or Max plan). To bill a
   metered API key instead, switch the `claude_code_oauth_token` input in
   `.github/workflows/claude-code.yml` back to `anthropic_api_key` and add
   an `ANTHROPIC_API_KEY` secret from console.anthropic.com.
3. Paste your LiveKit Docs MCP URL into `.claude/mcp.json`.

## What a demo looks like

Every demo lives at `demos/<slug>/` and carries these files:

| File | What it is |
| --- | --- |
| `agent.py` | The voice agent, copied from `templates/livekit-base/` and customized. |
| `pyproject.toml` | uv-managed runtime dependencies. Template defaults plus any demo extras. |
| `.env.example` | The credentials the demo uses, ready to copy to `.env`. |
| `README.md` | Short visitor entry: one-line hook, what it does, four uv commands, recording placeholder. |
| `playground.json` | Only when the demo emits playground UI events. Title, category, description, who_for, required_credentials, ui_components, released, stack. |
| `blog.md` | Required build writeup: frontmatter plus a markdown body in the frozen subset. Set `"blog": true` in `playground.json`. Renders on the demo's playground page. |

The writeup is plain markdown only (no raw HTML, no em dashes). To add an image
or diagram, host it at a public internet URL and link it inline like
`![alt](https://your-host/diagram.svg)`; image files are not committed to the
repo and inline `<svg>` or HTML is stripped. No `reel.md` or other marketing
content here; that lives elsewhere.

The slug is short, kebab-case, descriptive of the agent's job, never
ending in `agent` or `demo`. Examples: `url-summarizer`,
`drive-thru-coffee`, `intake-form-spanish`.

Each demo carries its own `.env.example` listing the keys it uses; copy it with
`cp .env.example .env`. The full six-key reference set is
`templates/livekit-base/.env.example`.

## Where metadata goes

`catalog.json` at the repo root is **derived** from each demo's
`playground.json`. The pre-commit hook (`scripts/build_catalog.py`)
rebuilds it on every commit that touches `demos/`. Do not hand-edit
`catalog.json`. Edit the demo's `playground.json` instead.

A `playground.json` looks like:

```json
{
  "title": "Drive-thru coffee",
  "category": "restaurant",
  "description": "Takes a coffee order, modifies items mid-flow, totals the cart.",
  "who_for": "Cafes that want voice ordering without ripping out their POS.",
  "recording_url": null,
  "required_credentials": [
    "openai_api_key",
    "deepgram_api_key",
    "cartesia_api_key",
    "livekit_url",
    "livekit_api_key",
    "livekit_api_secret"
  ],
  "ui_components": ["Order", "Total", "Checkout"],
  "released": "2026-05-12",
  "stack": { "stt": "deepgram", "llm": "openai", "tts": "cartesia" }
}
```

`released` is the demo's ship date (`YYYY-MM-DD`). The playground sorts
newest-first from it and badges demos shipped in the last 14 days as new. Stamp
it once with the ship date when the demo lands.

`stack` names the providers per role, lowercase. The playground shows it on the
card and builds a provider filter from it. A realtime model that does all three
roles repeats itself.

## Build budget

- Under two hours, end to end (code, record, write, ship).
- Under 300 lines of net new code on top of `templates/livekit-base/`.
- Never modify the template from inside a demo folder. Template changes
  are their own commit.

If a demo cannot fit, it becomes either a template extension under
`templates/` or a future milestone. Not a demo.

## The daily loop

The canonical entry point. Works from a phone.

1. A GitHub Actions cron opens an issue each morning titled
   `what are we building today? YYYY-MM-DD`. The body is a five-field
   template: slug, one-line hook, category, stack (STT/LLM/TTS),
   tools or UI components.
2. Fill in the body. Then comment `@claude scaffold this when ready`.
3. The Claude Code GitHub Action invokes the `demo-builder` subagent,
   scaffolds `demos/<slug>/{agent.py, pyproject.toml, README.md}`
   (plus `playground.json` when UI is listed), opens a PR titled
   `feat(demo): <slug> with <stt>/<llm>/<tts> stack`.
4. Review the PR. Merge.

For offline work, the same subagent is reachable as `/build` in a
local Claude Code session.

## Adding a demo by hand (offline fallback)

If you are not using the GitHub flow:

1. Pick a slug. Confirm the idea fits the cookbook.
2. Create the demo folder and copy the draft files:

   ```sh
   mkdir -p demos/<slug>
   cp templates/livekit-base/agent.py demos/<slug>/agent.py
   cp templates/livekit-base/pyproject.toml demos/<slug>/pyproject.toml
   ```

3. Edit the `name` and `description` fields in `pyproject.toml`.
   Trim the `livekit-agents[...]` extras to the providers the demo
   uses.
4. Customize `agent.py`. The usual edits: the `Assistant` class name
   and `instructions` string, one or more `@function_tool` methods,
   optional provider swap (update imports plus `AgentSession`
   constructor).
5. When the demo emits playground UI events, add a `playground.json`
   manifest.
6. Write a short `README.md` (hook, what it does, four uv commands,
   recording placeholder).
7. Link the demo's folder under its category in [README.md](README.md).
   Use a bold demo name and a one-line description so a visitor sees
   what it does without clicking.

## Tech stack

- Python 3.11.
- uv for dependency workflows. Dependencies in `pyproject.toml`.
- ruff for linting and formatting. Never black plus flake8.
- livekit-agents 1.x with direct provider plugins (Deepgram, OpenAI,
  Cartesia, Silero, turn-detector).
- No per-demo frontend. Demos run against the public LiveKit Agents
  Playground or a local clone of
  [livekit-examples/agent-starter-react](https://github.com/livekit-examples/agent-starter-react).

## Commits

Conventional commits, scoped:

- `feat(demo): drive-thru coffee with totals tool`
- `chore(template): bump livekit-agents to 1.6`
- `docs: clarify quick-start step 3`
- `fix(demo): correct cartesia voice ID in url-summarizer`

Subject line: imperative mood, lowercase, under 70 characters. Body
explains why, not what. Reference Linear issues on a trailing
`Refs MAH-NNN` line when applicable.

Do not commit `.env` files, lockfiles under `templates/`, Descript
exports, or binary artifacts over 1 MB.

## Style

Direct, technical, slightly opinionated. Skip qualifiers (very, really,
just, simply) and hedges (might, perhaps, maybe). Prefer short
sentences. Show code.

No em dashes. Use colons, periods, semicolons, or parentheses.

## Where things go

- A pattern reused across demos: in `templates/livekit-base/`.
- A pattern needing more than the template provides: a separate
  template extension under `templates/`, cleared with the operator first.
- A one-off voice idea: a demo under `demos/<slug>/`.
- Subagents, slash commands, hooks: under `.claude/`. Filled by M2.

## License

Apache 2.0. By contributing you agree your contribution will ship under
the same license.
