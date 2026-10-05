# Repository instructions

This is a public collection of runnable voice agents. Make it easy for someone
to contribute working code. Follow CONTRIBUTING.md.

## Example layout

Each `demos/<slug>/` needs `agent.py`, `pyproject.toml`, `.env.example`, and a
short `README.md`. Keep existing dependency locks and Python version pins.
Extra Python modules, tests, and runtime data are fine when the example needs
them. Never require a blog, tutorial, UI manifest, category, generated artwork,
or catalog entry. Website copy belongs to Mahimai's private website repository.

Use Python 3.11+, uv, and Ruff. Prefer the small LiveKit starter under
`templates/livekit-base`. Do not add a framework or shared service for a small
example. Never commit credentials or personal data. Keep paid evaluations
maintainer-triggered; outside contributors should not need our API secrets.

Keep READMEs practical: what it does, credentials, commands, and any extra
setup. Preserve working agent behavior when cleaning documentation. Existing
UI data events are optional integrations, not contribution requirements.

Use lowercase feat/ or fix/ branch names and conventional commit subjects.
No AI co-author trailers or generated-by footers. No em dashes in prose.
Hosting configuration is maintainer-owned and is not part of a new example.
