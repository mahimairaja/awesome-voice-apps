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


class ValidateReleasedTests(unittest.TestCase):
    def test_none_passes(self):
        bc.validate_released(Path("x"), None)  # no raise

    def test_valid_date_passes(self):
        bc.validate_released(Path("x"), "2026-05-12")  # no raise

    def test_non_string_raises(self):
        with self.assertRaises(ValueError):
            bc.validate_released(Path("x"), 20260512)

    def test_non_canonical_raises(self):
        with self.assertRaises(ValueError):
            bc.validate_released(Path("x"), "2026-5-1")

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            bc.validate_released(Path("x"), "not-a-date")

    def test_impossible_date_raises(self):
        with self.assertRaises(ValueError):
            bc.validate_released(Path("x"), "2026-13-40")


class ReleasedPassthroughTests(unittest.TestCase):
    def _load(self, extra: dict) -> dict:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "playground.json"
            p.write_text(json.dumps({**PLAYGROUND, **extra}), encoding="utf-8")
            return bc.load_demo(p)

    def test_released_copied_when_present(self):
        self.assertEqual(self._load({"released": "2026-05-12"})["released"], "2026-05-12")

    def test_released_absent_when_missing(self):
        self.assertNotIn("released", self._load({}))

    def test_malformed_released_in_file_raises(self):
        with self.assertRaises(ValueError):
            self._load({"released": "nope"})


if __name__ == "__main__":
    unittest.main()
