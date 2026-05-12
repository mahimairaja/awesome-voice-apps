# CLAUDE.md

Operating instructions for any Claude session working in this repo.
Auto loaded by Claude Code. The shorter and sharper this file is, the
better the cookbook holds up.

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
| Package manager | uv with requirements.txt inputs |
| Linter / formatter | ruff (never black plus flake8) |
| Voice runtime | livekit-agents 1.x with direct provider plugins |
| Default STT / LLM / TTS | Deepgram Nova-3 / OpenAI gpt-4o-mini / Cartesia Sonic-2 |
| VAD | Silero |
| Turn detector | LiveKit MultilingualModel |
| Frontend | None per demo. Connect via the public LiveKit Agents Playground or a local clone of `livekit-examples/agent-starter-react`. |

A branded playground is planned for M1. Until it ships, demos always
run against the public LiveKit Agents Playground or a local
agent-starter-react.

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
- No `Co-Authored-By: Claude` trailers, no `Generated with Claude Code`
  footer, no robot emoji, no AI attribution. Commits read as if Mahimai
  authored them directly.

## File conventions

A finished demo is the folder `demos/<slug>/` containing
exactly two files:

| File | Purpose |
| --- | --- |
| `agent.py` | The voice agent. Copied from the template, then customized. |
| `requirements.txt` | pip-format runtime deps. Same as template plus any demo-specific extras. |

The `<slug>` is short, kebab-case, descriptive. Examples:
`url-summarizer`, `drive-thru-coffee`, `intake-form-spanish`. No
trailing words like `agent` or `demo`.

The shared six-key environment example lives at
`templates/livekit-base/.env.example`. Demo folders do not carry their
own env examples unless a demo needs extra credentials and the operator
clears the exception first.

When a demo ships, link the demo's `agent.py` under the matching
category in `README.md`. Drop a bold demo name and a one-line
description so a visitor sees what the demo does without clicking in.

## Where metadata goes

The root `catalog.json` is the source of truth for playground metadata.
Every shipped demo gets one entry keyed by its slug. Keep the slug equal
to the folder name. The category must match one of the ten cookbook
categories.

```json
{
  "drive-thru-coffee": {
    "title": "Drive-thru coffee",
    "category": "Drive-thru & Ordering",
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
}
```

## Categories

Demos are tagged with one of: Receptionist & Booking, Drive-thru &
Ordering, Customer Support, Companion & Coaching, Education, Data
Extraction, Tool Calling, Multilingual, Multi-Agent, Telephony & SIP.

Add a new category only when no existing one fits, and only after
clearing it with the operator.

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
- A subagent, slash command, or hook: into `.claude/`. Currently empty.
  Lands in M2.
- A planning artifact (Refinery doc, Foundry blueprint, design notes):
  Linear, not the repo.

## Out of scope for this repo

- Anything ShipVoice shaped. Separate repo, separate product.
- The branded playground. Separate repo, lands in M1.
- Build automation beyond the playground rebuild hook.
- Per-demo custom frontends. Default is the run instructions.
- An automated test suite for demos. Demos that need tests can add them
  per-demo, but no top-level pytest harness.

## When the operator asks for the next demo

1. Skim the README's category sections and the most recent folders
   under `demos/` so you do not propose a near duplicate.
2. Propose three demo ideas with: hook, category, stack delta from
   `templates/livekit-base/`, build estimate.
3. Wait for the operator to pick one.
4. Only then start coding.
