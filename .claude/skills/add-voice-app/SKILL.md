---
name: add-voice-app
description: >
  Add a new runnable voice agent to this repository as demos/<slug>, from a
  person's idea through to a pull request. TRIGGER when the user wants to
  contribute, add, build or submit a voice app, voice agent or demo to
  awesome-voice-apps. DO NOT TRIGGER for changes to the hosted playground,
  deployment, evals or existing demos.
---

# Add a voice app

The full brief, kept in step with the website docs, is at
https://mahimai.ca/playground/docs/agent.md. Read it if you can reach the web;
this file is the same procedure for offline use.

1. Restate the idea in two sentences (who calls, what the agent does, which
   tools it needs) and pick a lowercase, hyphenated slug.
2. `git checkout -b feat/<slug>` (fork first with `gh repo fork` if this clone
   is not the person's fork).
3. `cp -R templates/livekit-base demos/<slug>`. Read `CONTRIBUTING.md` and one
   or two existing demos for tool patterns.
4. Set `name = "<slug>"` in `pyproject.toml`; write `agent.py` (instructions,
   `@function_tool`s over fake in-memory data, `agent_name="<slug>"`); list
   every variable in `.env.example` with empty credential values; write a
   short `README.md` with the `uv sync` / `uv run python agent.py console`
   commands.
5. Optional: `models.toml` when the use case needs specific models (copy the
   commented example in the template; ids come from `playground/model_menu.py`).
6. Run `python3 scripts/check_demo.py demos/<slug> --json` and apply each
   failing result's `fix` until `ready` is true.
7. Ask the person to fill `.env` themselves and run the console command. Never
   ask for keys in chat; never print or commit `.env`.
8. Commit `feat(demos): add <slug>`, push, and `gh pr create --repo
   mahimairaja/awesome-voice-apps` with what it does, its tools and how it was
   tested.
9. Give the person the PR link. After merge their contributor card is at
   https://mahimai.ca/playground/contributors/<github-login>.

Change only `demos/<slug>/`. No credentials, personal data, AI co-author
trailers or em dashes.
