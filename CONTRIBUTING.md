# Contributing

This cookbook is one operator's working log of voice AI demos.
External contributions are welcome but not the primary loop. If you
want to add a demo or fix something, read this once and you have all
the context you need.

For the AI-collaborator version of these conventions, see
[CLAUDE.md](CLAUDE.md). The two files agree.

## What a demo looks like

Every demo lives at `demos/YYYY-MM-DD-<slug>/` and contains exactly six
files:

| File | What it is |
| --- | --- |
| `agent.py` | The voice agent, copied from `templates/livekit-base/` and customized. |
| `pyproject.toml` | uv-managed dependencies. Template defaults plus any demo extras. |
| `.env.example` | The credentials a reader needs to fill in to run the demo. |
| `README.md` | One paragraph on the idea, who it is for, four-step run instructions, walkthrough link. |
| `blog.md` | 800 to 1200 word walkthrough post. Sections: problem, stack, walkthrough, try it. |
| `reel.md` | 30 to 45 second vertical-video script. Hook in the first three seconds. |

The slug is short, kebab-case, descriptive of the agent's job, never
ending in `agent` or `demo`. Examples: `url-summarizer`,
`drive-thru-coffee`, `intake-form-spanish`.

## Build budget

- Under two hours, end to end (code, record, write, ship).
- Under 300 lines of net new code on top of `templates/livekit-base/`.
- Never modify the template from inside a demo folder. Template changes
  are their own commit.

If a demo cannot fit, it becomes either a template extension under
`templates/` or a future milestone. Not a demo.

## Adding a demo

1. Pick a date (today) and a slug. Confirm with the operator that the
   idea fits the cookbook.
2. Copy the template:

   ```sh
   cp -r templates/livekit-base demos/YYYY-MM-DD-<slug>
   ```

3. Customize `agent.py`. The usual edits: the `Assistant` instructions
   string, one or more `@function_tool` methods, optional provider swap.
4. Update `pyproject.toml` if you added or removed a `livekit-agents`
   extra, then run `uv sync`.
5. Update `.env.example` to reflect any extra credentials the demo
   needs.
6. Write the four prose files: `README.md`, `blog.md`, `reel.md`. Record
   the walkthrough.
7. Link the demo folder under its category in
   [README.md](README.md). Use a bold demo name and a one-line
   description so a visitor sees what it does without clicking.

## Tech stack

- Python 3.11.
- uv for dependencies. Never pip.
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
- `fix(demo): correct cartesia voice ID in 2026-05-14-url-summarizer`

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
- A one-off voice idea: a demo under `demos/YYYY-MM-DD-<slug>/`.
- Subagents, slash commands, hooks: under `.claude/`. Empty for now;
  fills up during M2.

## License

Apache 2.0. By contributing you agree your contribution will ship under
the same license.
