# Add a voice app

Build a working example and open a pull request. You do not need to write a
blog, tutorial, website description, or UI manifest. We handle those separately.

1. Copy the starter: `cp -R templates/livekit-base demos/your-app`.
2. Change the project name in `pyproject.toml` and customize `agent.py`.
3. List the required variables in `.env.example`, with empty values.
4. Update the short README with what it does and how to run it.
5. Run it locally, then open a PR describing what you tested.

```text
demos/your-app/
  agent.py
  pyproject.toml
  .env.example
  README.md
```

Dependency lockfiles and Python version pins are welcome. Add helper modules,
tests, or runtime data only when the example needs them. Keep the example
self-contained. Never commit your `.env`, recordings of real people, or API keys.

Use `uvx ruff==0.15.16 check .` and `uvx ruff==0.15.16 format --check .` to check Python changes.
Optional commit hooks: `make hooks`. Paid provider evaluations are run manually
by maintainers; no paid account or website integration is required to submit a PR.

Maintainers review the code, write website content, and choose which examples
to host. A contribution does not need to be hosted to be useful.

Contributions use the repository's [Apache 2.0 license](LICENSE).
