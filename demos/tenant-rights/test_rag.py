"""Unit tests for the portable retrieval core. No LiveKit, no network.

Run directly:   uv run --no-project python test_rag.py
Or with pytest: pytest test_rag.py

These guard the grounding boundary: a matching query clears the floor and
returns the right section, an unrelated query falls below it (the refusal
path), and an empty index is never treated as covered.
"""

import numpy as np

from rag import retrieve


def _fake_index() -> dict:
    vectors = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
    )
    return {
        "vectors": vectors,
        "texts": ["deposits section", "repairs section", "eviction section"],
        "labels": ["HUD guidance", "HUD guidance", "HUD guidance"],
    }


def test_matching_query_returns_section_above_floor() -> None:
    result = retrieve(_fake_index(), [0.9, 0.1, 0.0], k=2, floor=0.4)
    assert result.covered is True
    assert result.hits[0].text == "deposits section"
    assert result.best_score >= 0.4


def test_unrelated_query_falls_below_floor() -> None:
    # Equidistant from every chunk, so no single section is a strong match.
    result = retrieve(_fake_index(), [1.0, 1.0, 1.0], k=2, floor=0.7)
    assert result.covered is False
    assert result.best_score < 0.7


def test_empty_index_is_not_covered() -> None:
    index = {"vectors": np.zeros((0, 3), dtype=np.float32), "texts": [], "labels": []}
    result = retrieve(index, [1.0, 0.0, 0.0])
    assert result.covered is False
    assert result.hits == []


def _run() -> None:
    test_matching_query_returns_section_above_floor()
    test_unrelated_query_falls_below_floor()
    test_empty_index_is_not_covered()
    print("ok: 3 passed")


if __name__ == "__main__":
    _run()
