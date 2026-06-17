# AGENTS.md

Operating instructions for any agent working in this repo (Claude
Code, Codex, Cursor, Aider, or a human). `CLAUDE.md` imports this
file so Claude Code auto-loads it. The shorter and sharper this file
is, the better the cookbook holds up.

## What this repo is

A public cookbook of voice AI demos. Mahimai is the sole operator. Each
demo is a self-contained folder under `demos/` and exists to:

1. Drive awareness for Mahimai AI (the voice agent agency).
2. Seed ShipVoice (the paid voice AI starter kit).

If a piece of work does not advance one of those two goals, it does not
belong in this repo.

## Default mode: brainstorm before code

When the operator opens a fresh session and asks for help, the default
mode is **brainstorming**, not coding. Confirm the demo idea fits the
constraints below, then propose the smallest path to shipped. Only start
writing code when the operator says go, or when the request is
unambiguously about execution.

## Tech stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.11 (`.python-version` pinned) |
| Package manager | uv (pyproject.toml per demo) |
| Linter / formatter | ruff (never black plus flake8) |
| Voice runtime | livekit-agents 1.x with direct provider plugins |
| Default STT / LLM / TTS | Deepgram Nova-3 / OpenAI gpt-4o-mini / Cartesia Sonic-2 |
| VAD | Silero |
| Turn detector | LiveKit MultilingualModel |
| Frontend | None per demo. Visitors run the agent locally and talk to it at `playground.mahimai.ca/demos/<slug>`, pasting their own three LiveKit values. |

The branded playground at playground.mahimai.ca is live. Each shipped
demo runs at playground.mahimai.ca/demos/<slug>; point READMEs and the
agent.py run block there, not at the generic LiveKit Agents Playground.

## Hard constraints

- Build budget: each demo ships in under two hours, end to end (code,
  record, write, publish).
- Code budget: under 300 lines of net new code per demo, on top of
  `templates/livekit-base/`.
- Never modify `templates/livekit-base/` from inside a demo folder. If
  the template needs a change, that is a separate commit on the template
  itself.
- No backticks in shell prompts you suggest the operator paste.
- No em dashes. Use colons, periods, semicolons, or parentheses.
- No AI co-author trailers (no `Co-Authored-By: Claude`, no
  `Co-Authored-By: Codex`), no "Generated with" footers, no robot
  emoji, no AI attribution. Commits read as if Mahimai authored them
  directly.

## File conventions

A demo lives at `demos/<slug>/`. The `<slug>` is short, kebab-case,
descriptive. Examples: `url-summarizer`, `drive-thru-coffee`,
`intake-form-spanish`. No trailing words like `agent` or `demo`.

Every demo carries these files:

| File | Purpose |
| --- | --- |
| `agent.py` | The voice agent. Copied from the template, then customized. |
| `pyproject.toml` | uv-managed runtime deps. Template defaults plus demo-specific extras. |
| `.python-version` | Python 3.11 pin, copied verbatim from the template. |
| `README.md` | Short visitor entry: one-line hook, what it does, four uv commands, recording placeholder. |
| `playground.json` | Only when the demo emits playground UI events. Title, category, description, who_for, required_credentials, ui_components. |
| `blog.md` | Build writeup: frontmatter (title, summary) plus a markdown body in the frozen subset. Renders on the demo's playground page. Always written by the subagent; optional when scaffolding by hand. |

The shared six-key environment example lives at
`templates/livekit-base/.env.example`. Demo folders do not carry their
own env examples unless a demo needs extra credentials and the operator
clears the exception first.

`blog.md` is the one writeup file a demo may carry. The subagent always
scaffolds it; a human scaffolding by hand may omit it. It is a build
writeup for builders: the problem, the stack choice, the interesting code,
the one gotcha, a run link. The `README.md` stays the run instructions;
`blog.md` adds the story. `reel.md` and other marketing content still do
not belong in this repo; the operator writes those elsewhere.

A `blog.md` is YAML frontmatter plus a markdown body.

Frontmatter keys (flat, simple scalars, one per line):

- `title` (required)
- `summary` (required, one or two sentences, used as the page meta description)
- `cover` (optional external image URL; no repo binaries)
- `canonical` (optional URL for syndication; defaults to the demo page)
- `author` (optional, defaults to Mahimai)

No published date: posts carry no visible date.

Allowed markdown: headings, paragraphs, bold and italic, links, inline
code, fenced code blocks with a language, bullet and numbered lists,
blockquotes, and external images. No raw HTML. Images must be hosted at a
public internet URL and linked inline like `![alt](https://host/file.svg)`;
image files are not committed to the repo and inline `<svg>` or HTML is
stripped, so the URL must be publicly fetchable. This subset is frozen the
same way `ui_components` is: a writeup that needs more is a deliberate
playground change, not a demo declaration.

When a demo ships, link the demo folder under the matching category in
`README.md`. Drop a bold demo name and a one-line description so a
visitor sees what the demo does without clicking in.

## Demo quality bar

The hard parts (the LiveKit API, the playground UI prop shapes, the
conventions) usually come out right. The recurring gaps are in application
logic and state. Whoever builds a demo (the `@claude` action, the
`demo-builder` subagent, or a human) verifies these before it is done:

- State invariants. If tools mutate shared state (a cart, inventory, a
  booking), keep one coherent model and make every tool preserve the invariant:
  a mutation that removes an item has an inverse that restores it, and a
  single-resource booking refuses a second create instead of overwriting.
- UI mirrors state. Re-publish the relevant component after every state change,
  not just on the read path, and publish an empty component to clear it when its
  data is empty. The playground UI is the showcase; a list that disagrees with
  state is the most visible bug.
- Prompt matches tools. Only promise in the instructions string what a tool or
  parameter actually implements.
- Fresh data. Generate time-relative values (dates, deadlines) from datetime at
  startup, not hard-coded literals that go stale.
- Edge cases. Each tool handles empty results, duplicates, a missing
  prerequisite, and a no-op with a clear, honest message.
- Run docs match the stack. The README and the `agent.py` run block name only
  the keys the demo uses, and `required_credentials` lists exactly those.

## Where metadata goes

`catalog.json` at the repo root is the public catalog. It is **derived**
from each demo's `playground.json` and rebuilt by the pre-commit hook
(`scripts/build_catalog.py`). Do not hand-edit `catalog.json`. Edit the
demo's `playground.json` instead and let the hook rewrite the catalog.

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
  "ui_components": ["Order", "Total", "Checkout"]
}
```

`ui_components` and any `publish_ui_event` calls must use only the
components the playground renders. The supported set and their prop
shapes are documented in `docs/playground-components.md`. The playground
skips any component outside that set, so do not invent names; if a demo
needs UI beyond the set, that is a deliberate playground change, not a
demo declaration.

## Categories

A demo is tagged with one industry it serves. Single lowercase word, one
of: healthcare, legal, finance, realestate, hospitality, restaurant,
automotive, education, retail, recruiting, construction, travel, fitness,
beauty, logistics, insurance, nonprofit, gov, media.

These are industries, not use cases. A multilingual healthcare intake is
`healthcare`, not a "multilingual" or "data extraction" tag. `restaurant`
is split from `hospitality` (hotels, bars, cafes); do not split
hospitality further until each sub-vertical has 5 or more demos. `gov` is
government and public sector.

Add a new tag only when no existing one fits, only after clearing it with
the operator, and keep the total inside 18 to 20.

## Commit conventions

Conventional commits, scoped to the affected area:

- `feat(demo): drive-thru coffee with totals tool` for a new demo
- `chore(template): bump livekit-agents to 1.6` for template updates
- `docs: clarify quick-start step 3` for repo-level docs
- `fix(demo): correct cartesia voice ID in url-summarizer`

Subject line: imperative mood, lowercase, under 70 characters. Body:
explain why, not what. Reference Linear issues with `Refs MAH-NNN` on a
trailing line when the change ties to one.

Do not stage `.env` files, lockfiles inside `templates/`, recordings
exported from Descript, or any binary artifacts over 1 MB.

## Voice and tone

The cookbook is written for indie hackers and agency founders who want
to build voice agents quickly. The voice is direct, technical, and
slightly opinionated. Skip qualifiers (very, really, just, simply) and
hedges (might, perhaps, maybe). Prefer short sentences. Show code.

In the agent's own instructions string, keep it short, plain text, no
markdown or emojis. Voice agents read the instructions out into
synthesis, so anything fancy bleeds through.

## What goes where

- A reusable pattern across many demos: into `templates/livekit-base/`.
- A pattern that needs more than the template provides: a template
  extension under `templates/`, not a demo. Cleared with the operator
  first.
- A one-off voice idea: a demo under `demos/<slug>/`.
- A subagent, slash command, or hook: into `.claude/`. M2 lives here.
  One subagent (`demo-builder`), one slash command (`/build`), one
  MCP config (`mcp.json`), and the catalog auto-maintenance hook.
  Other agents (Codex, Cursor) read `.claude/` via thin wrappers when
  they need the same context.
- A planning artifact (Refinery doc, Foundry blueprint, design notes,
  Superpowers specs): under `docs/superpowers/specs/` or Linear, not
  the cookbook surface.

## Out of scope for this repo

- Anything ShipVoice-shaped. Separate repo, separate product.
- The branded playground. Separate repo, lands in M1.
- Per-demo custom frontends. Default is the run instructions.
- An automated test suite for demos. Demos that need tests can add them
  per-demo, but no top-level pytest harness.

## The daily loop

1. A GitHub Actions cron opens an issue each morning titled
   `what are we building today? YYYY-MM-DD`. Template body has five
   fields: slug, one-line hook, category, stack (STT/LLM/TTS), tools
   or UI components.
2. The operator fills in the issue body and comments
   `@claude scaffold this when ready`.
3. The Claude Code GitHub Action fires, invokes the `demo-builder`
   subagent, scaffolds `demos/<slug>/{agent.py, pyproject.toml,
   README.md, blog.md}` (plus `playground.json` when UI is listed),
   and opens a PR titled `feat(demo): <slug> with <stt>/<llm>/<tts> stack`.
4. The operator reviews and merges.

For offline work, the same subagent is reachable as `/build` in a
local Claude Code session. Accepts:

- `/build <slug>: <stt>, <llm>, <tts>. <hook>` (inline spec)
- `/build #NN` (reads the GitHub issue)
- `/build` (asks for the missing fields interactively)

The subagent consults the LiveKit Docs MCP for each provider it does
not recognize; there is no baked-in provider table.
