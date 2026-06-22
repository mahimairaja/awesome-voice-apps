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
