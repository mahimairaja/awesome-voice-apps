#!/usr/bin/env python3
"""Regenerate catalog.json from each demo's playground.json.

The cookbook keeps per-demo metadata in `demos/<slug>/playground.json`.
The root `catalog.json` is a derived map keyed by slug. This script
rebuilds the root file from the per-demo files. It is invoked by the
pre-commit hook so the catalog never drifts from what is on disk.

Run it directly to preview:

    uv run --no-project python scripts/build_catalog.py
    uv run --no-project python scripts/build_catalog.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMOS_DIR = REPO_ROOT / "demos"
CATALOG_PATH = REPO_ROOT / "catalog.json"

CATALOG_FIELDS = (
    "title",
    "category",
    "description",
    "who_for",
    "recording_url",
    "required_credentials",
    "ui_components",
)


def load_demo(playground_path: Path) -> dict:
    with playground_path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return {field: raw[field] for field in CATALOG_FIELDS if field in raw}


def build_catalog() -> dict:
    catalog: dict = {}
    for demo_dir in sorted(p for p in DEMOS_DIR.iterdir() if p.is_dir()):
        playground = demo_dir / "playground.json"
        if not playground.exists():
            continue
        catalog[demo_dir.name] = load_demo(playground)
    return catalog


def render(catalog: dict) -> str:
    return json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if catalog.json is out of date; do not write",
    )
    args = parser.parse_args()

    catalog = build_catalog()
    rendered = render(catalog)

    if args.check:
        current = (
            CATALOG_PATH.read_text(encoding="utf-8") if CATALOG_PATH.exists() else ""
        )
        if current == rendered:
            return 0
        sys.stderr.write("catalog.json is out of date. Run scripts/build_catalog.py.\n")
        return 1

    CATALOG_PATH.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
