#!/usr/bin/env python3
"""Stdlib unit tests for scripts/build_catalog.py.

Run directly (no pytest, no top-level test harness):

    python3 scripts/test_build_catalog.py -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_catalog as bc  # noqa: E402

PLAYGROUND = {
    "title": "Sample",
    "category": "retail",
    "description": "d",
    "who_for": "w",
    "recording_url": None,
    "required_credentials": [],
    "ui_components": [],
}


class ParseFrontmatterTests(unittest.TestCase):
    def test_parses_simple_fields(self):
        text = "---\ntitle: Hello\nsummary: A short summary\n---\nbody here\n"
        self.assertEqual(
            bc.parse_frontmatter(text),
            {"title": "Hello", "summary": "A short summary"},
        )

    def test_strips_surrounding_quotes(self):
        text = '---\ntitle: "Quoted title"\nsummary: ok\n---\n'
        self.assertEqual(bc.parse_frontmatter(text)["title"], "Quoted title")

    def test_skips_empty_values(self):
        text = "---\ntitle: T\nsummary: S\ncover:\n---\nbody\n"
        self.assertNotIn("cover", bc.parse_frontmatter(text))

    def test_missing_open_fence_raises(self):
        with self.assertRaises(ValueError):
            bc.parse_frontmatter("title: no fence\n")

    def test_unclosed_fence_raises(self):
        with self.assertRaises(ValueError):
            bc.parse_frontmatter("---\ntitle: x\n")


class ValidateBlogTests(unittest.TestCase):
    def _write(self, d: str, text: str) -> Path:
        p = Path(d) / "blog.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_missing_required_field_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "---\ntitle: Only title\n---\nbody\n")
            with self.assertRaises(ValueError):
                bc.validate_blog(p)

    def test_valid_passes(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "---\ntitle: T\nsummary: S\n---\nbody\n")
            bc.validate_blog(p)  # no raise


class BuildCatalogFlagTests(unittest.TestCase):
    def _demo(self, root: Path, slug: str, blog: str | None) -> None:
        demo = root / slug
        demo.mkdir(parents=True)
        (demo / "playground.json").write_text(json.dumps(PLAYGROUND), encoding="utf-8")
        if blog is not None:
            (demo / "blog.md").write_text(blog, encoding="utf-8")

    def test_blog_flag_set_when_present(self):
        with tempfile.TemporaryDirectory() as d:
            demos = Path(d) / "demos"
            self._demo(demos, "with-blog", "---\ntitle: T\nsummary: S\n---\nbody\n")
            orig = bc.DEMOS_DIR
            bc.DEMOS_DIR = demos
            try:
                catalog = bc.build_catalog()
            finally:
                bc.DEMOS_DIR = orig
            self.assertTrue(catalog["with-blog"].get("blog"))

    def test_blog_flag_absent_when_no_file(self):
        with tempfile.TemporaryDirectory() as d:
            demos = Path(d) / "demos"
            self._demo(demos, "no-blog", None)
            orig = bc.DEMOS_DIR
            bc.DEMOS_DIR = demos
            try:
                catalog = bc.build_catalog()
            finally:
                bc.DEMOS_DIR = orig
            self.assertNotIn("blog", catalog["no-blog"])


if __name__ == "__main__":
    unittest.main()
