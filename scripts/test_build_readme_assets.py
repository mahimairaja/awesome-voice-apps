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
