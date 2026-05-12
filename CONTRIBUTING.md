# Contributing

This cookbook is one operator's working log of voice AI demos.
External contributions are welcome but not the primary loop. If you
want to add a demo or fix something, read this once and you have all
the context you need.

For the AI-collaborator version of these conventions, see
[CLAUDE.md](CLAUDE.md). The two files agree.

## What a demo looks like

Every demo lives at `demos/<slug>/` and contains exactly two
files:

| File | What it is |
| --- | --- |
| `agent.py` | The voice agent, copied from `templates/livekit-base/` and customized. |
| `requirements.txt` | pip-format runtime dependencies. Template defaults plus any demo extras. |

The slug is short, kebab-case, descriptive of the agent's job, never
ending in `agent` or `demo`. Examples: `url-summarizer`,
`drive-thru-coffee`, `intake-form-spanish`.

The shared env example is `templates/livekit-base/.env.example`. Demo
folders do not duplicate it.

## Where metadata goes

The root `catalog.json` holds the playground metadata for every shipped
demo. The file is keyed by slug, and the slug must match the demo folder.
Validate changes against `catalog.schema.json` before committing.

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

## Build budget

- Under two hours, end to end (code, record, write, ship).
- Under 300 lines of net new code on top of `templates/livekit-base/`.
- Never modify the template from inside a demo folder. Template changes
  are their own commit.

If a demo cannot fit, it becomes either a template extension under
`templates/` or a future milestone. Not a demo.

## Adding a demo

1. Pick a slug. Confirm with the operator that the idea fits the
   cookbook.
2. Create the demo folder and copy the two demo files:

   ```sh
   mkdir -p demos/<slug>
   cp templates/livekit-base/agent.py demos/<slug>/agent.py
   cp templates/livekit-base/requirements.txt demos/<slug>/requirements.txt
   ```

3. Customize `agent.py`. The usual edits: the `Assistant` instructions
   string, one or more `@function_tool` methods, optional provider swap.
4. Trim `requirements.txt` to the providers the demo uses.
5. Add the demo entry to `catalog.json`.
6. Link the demo's `agent.py` under its category in [README.md](README.md).
   Use a bold demo name and a one-line description so a visitor sees
   what it does without clicking.

## Tech stack

- Python 3.11.
- uv for dependency workflows. Dependencies stay in requirements files.
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
- Subagents, slash commands, hooks: under `.claude/`. Empty for now;
  fills up during M2.

## License

Apache 2.0. By contributing you agree your contribution will ship under
the same license.
