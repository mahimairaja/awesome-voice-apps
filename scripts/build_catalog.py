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
from datetime import date
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
    "released",
)

BLOG_FILENAME = "blog.md"
REQUIRED_BLOG_FIELDS = ("title", "summary")


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse the closed, flat key:value frontmatter between the leading --- fences.

    Empty-valued keys are skipped. Values may carry one optional layer of
    surrounding single or double quotes, which is stripped. Raises ValueError
    if the opening or closing --- fence is missing.
    """
    if not text.startswith("---"):
        raise ValueError("blog.md must start with a --- frontmatter fence")
    lines = text.splitlines()
    closing = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing = i
            break
    if closing is None:
        raise ValueError("blog.md frontmatter is not closed with a --- fence")
    fields: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError(f"blog.md frontmatter line is not key: value -> {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            fields[key] = value
    return fields


def validate_blog(blog_path: Path) -> None:
    """Validate a demo's blog.md frontmatter. Raises ValueError if malformed."""
    text = blog_path.read_text(encoding="utf-8")
    fields = parse_frontmatter(text)
    missing = [f for f in REQUIRED_BLOG_FIELDS if not fields.get(f)]
    if missing:
        raise ValueError(
            f"{blog_path}: blog.md frontmatter missing required field(s): "
            f"{', '.join(missing)}"
        )


def validate_released(playground_path: Path, released: object) -> None:
    """Validate an optional `released` value. Raises ValueError if present and not
    a canonical YYYY-MM-DD date string."""
    if released is None:
        return
    if not isinstance(released, str):
        raise ValueError(f"{playground_path}: released must be a YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(released)
    except ValueError as exc:
        raise ValueError(f"{playground_path}: released must be YYYY-MM-DD ({exc})") from exc
    if parsed.isoformat() != released:
        raise ValueError(
            f"{playground_path}: released must be canonical YYYY-MM-DD, got {released!r}"
        )


def load_demo(playground_path: Path) -> dict:
    with playground_path.open("r", encoding="utf-8") as fh:
        try:
            raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{playground_path}: invalid JSON ({exc.msg} at line {exc.lineno})"
            ) from exc
    if not isinstance(raw, dict):
        raise ValueError(
            f"{playground_path}: expected a JSON object, got {type(raw).__name__}"
        )
    entry = {field: raw[field] for field in CATALOG_FIELDS if field in raw}
    validate_released(playground_path, entry.get("released"))
    return entry


def build_catalog() -> dict:
    catalog: dict = {}
    for demo_dir in sorted(p for p in DEMOS_DIR.iterdir() if p.is_dir()):
        playground = demo_dir / "playground.json"
        if not playground.exists():
            continue
        entry = load_demo(playground)
        blog_path = demo_dir / BLOG_FILENAME
        if blog_path.exists():
            validate_blog(blog_path)
            entry["blog"] = True
        catalog[demo_dir.name] = entry
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
