import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_readme_assets as gen  # noqa: E402


def test_speech_wave_is_deterministic_and_a_path():
    a = gen.speech_wave("clinic-scheduler", 0, 400, 60, 20)
    b = gen.speech_wave("clinic-scheduler", 0, 400, 60, 20)
    assert a == b
    assert a.startswith("M")
    assert " L" in a


def test_speech_wave_differs_by_seed():
    assert gen.speech_wave("a", 0, 400, 60, 20) != gen.speech_wave("b", 0, 400, 60, 20)


def test_graticule_is_a_group_of_lines():
    g = gen.graticule(0, 0, 100, 100, 50, 0.06)
    assert g.startswith("<g") and g.endswith("</g>")
    assert g.count("<line") == 6  # x at 0,50,100 + y at 0,50,100


from xml.dom.minidom import parseString  # noqa: E402

ENTRY = {
    "category": "healthcare",
    "description": "Books a doctor appointment by voice, finds open slots, and handles reschedules.",
}


def test_truncate_adds_ellipsis_past_limit():
    assert gen.truncate("short", 52) == "short"
    out = gen.truncate("x" * 80, 52)
    assert len(out) == 52 and out.endswith("…")


def test_render_card_is_wellformed_and_has_content():
    svg = gen.render_card("clinic-scheduler", ENTRY)
    parseString(svg)  # raises if not well-formed XML
    assert "clinic-scheduler" in svg
    assert "HEALTHCARE" in svg
    assert "Books a doctor appointment" in svg


def test_render_card_is_deterministic():
    assert gen.render_card("clinic-scheduler", ENTRY) == gen.render_card("clinic-scheduler", ENTRY)


def test_render_banner_wellformed_and_branded():
    svg = gen.render_banner()
    parseString(svg)
    assert "awesome-voice-apps" in svg
    assert 'width="1280"' in svg and 'height="320"' in svg


def test_render_pipeline_wellformed_with_nodes():
    svg = gen.render_pipeline()
    parseString(svg)
    for caption in ("hear", "think", "speak"):
        assert caption in svg


def test_banner_and_pipeline_are_deterministic():
    assert gen.render_banner() == gen.render_banner()
    assert gen.render_pipeline() == gen.render_pipeline()


def test_render_gallery_links_each_demo_in_order():
    html = gen.render_gallery(["a", "b", "c"])
    assert html.count("<img") == 3
    assert html.index('demos/a/') < html.index('demos/b/') < html.index('demos/c/')
    assert '<img src="assets/demos/a.svg" width="100%" alt="a">' in html
    # odd count pads the last row to two columns
    assert html.count('<td') == 4


def test_rewrite_gallery_replaces_between_markers():
    text = f"before\n{gen.GALLERY_START}\nOLD\n{gen.GALLERY_END}\nafter"
    out = gen.rewrite_gallery(text, "NEW")
    assert "OLD" not in out
    assert "NEW" in out
    assert out.startswith("before") and out.endswith("after")


def test_rewrite_gallery_requires_markers():
    import pytest

    with pytest.raises(ValueError):
        gen.rewrite_gallery("no markers here", "NEW")


def test_build_check_passes_then_detects_staleness(tmp_path, monkeypatch):
    # Point the generator at a throwaway repo layout.
    catalog = {"a": {"title": "A", "category": "healthcare", "description": "does a."}}
    (tmp_path / "catalog.json").write_text(__import__("json").dumps(catalog))
    (tmp_path / "assets" / "demos").mkdir(parents=True)
    (tmp_path / "README.md").write_text(
        f"top\n{gen.GALLERY_START}\nstale\n{gen.GALLERY_END}\nbottom\n"
    )
    monkeypatch.setattr(gen, "REPO", tmp_path)
    monkeypatch.setattr(gen, "CATALOG", tmp_path / "catalog.json")
    monkeypatch.setattr(gen, "ASSETS", tmp_path / "assets")
    monkeypatch.setattr(gen, "DEMO_ASSETS", tmp_path / "assets" / "demos")
    monkeypatch.setattr(gen, "README", tmp_path / "README.md")

    assert gen.build(check=True)  # stale before generating (non-empty list)
    assert gen.build(check=False) == []  # generate everything
    assert (tmp_path / "assets" / "banner.svg").exists()
    assert (tmp_path / "assets" / "demos" / "a.svg").exists()
    assert gen.build(check=True) == []  # now clean
    (tmp_path / "assets" / "demos" / "a.svg").write_text("mutated")
    assert gen.build(check=True)  # mutation detected
