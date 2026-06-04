"""Portable retrieval core for the tenant-rights demo.

No LiveKit imports. `retrieve` is pure numpy and is the unit-tested core.
The two embedding helpers call NVIDIA NIM and lazily import the openai client,
so this module imports cleanly without the openai package installed (which is
what lets test_rag.py run without the voice stack).

The same `retrieve` and `embed_query` lift into any other runtime unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
EMBED_MODEL = "nvidia/nv-embedqa-e5-v5"

# Cosine floor below which the question is treated as not covered by the
# documents, which triggers the agent's refuse-and-redirect behavior. Tuned
# empirically against nv-embedqa-e5-v5; raise it to refuse more aggressively.
DEFAULT_FLOOR = 0.4


@dataclass
class Hit:
    text: str
    source_label: str
    score: float


@dataclass
class RetrieveResult:
    hits: list[Hit]
    covered: bool
    best_score: float


def load_index(path: str | os.PathLike) -> dict:
    """Load a prebaked index written by build_index.py.

    build_index.py stores texts and labels as native numpy unicode arrays, not
    object arrays, so we load with allow_pickle=False. No pickle is involved,
    which keeps the load free of any arbitrary-code-execution surface.
    """
    data = np.load(path, allow_pickle=False)
    return {
        "vectors": np.asarray(data["vectors"], dtype=np.float32),
        "texts": [str(t) for t in data["texts"]],
        "labels": [str(label) for label in data["labels"]],
    }


def retrieve(
    index: dict,
    query_vec: list[float] | np.ndarray,
    k: int = 3,
    floor: float = DEFAULT_FLOOR,
) -> RetrieveResult:
    """Cosine top-k over the in-memory index.

    Returns the top hits plus a `covered` flag: True when the best score clears
    `floor`, False when nothing in the documents is close enough to answer.
    """
    vectors: np.ndarray = index["vectors"]
    if vectors.shape[0] == 0:
        return RetrieveResult(hits=[], covered=False, best_score=0.0)

    q = np.asarray(query_vec, dtype=np.float32)
    q = q / (float(np.linalg.norm(q)) + 1e-8)
    row_norms = np.linalg.norm(vectors, axis=1) + 1e-8
    scores = (vectors @ q) / row_norms

    k = max(1, min(k, scores.shape[0]))
    top = np.argsort(-scores)[:k]
    hits = [
        Hit(
            text=index["texts"][i],
            source_label=index["labels"][i],
            score=float(scores[i]),
        )
        for i in top
    ]
    best = hits[0].score
    return RetrieveResult(hits=hits, covered=best >= floor, best_score=best)


def _require_key() -> str:
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set; cannot reach NVIDIA NIM.")
    return key


_async_client = None


def _async_embed_client():
    """Build the NIM embeddings client once and reuse it across turns.

    Reusing one AsyncOpenAI keeps its keep-alive connection pool warm, so each
    turn skips a fresh TLS handshake and nothing is left dangling for the GC.
    The openai import stays lazy so this module imports without the package.
    """
    global _async_client
    if _async_client is None:
        from openai import AsyncOpenAI

        _async_client = AsyncOpenAI(base_url=NIM_BASE_URL, api_key=_require_key())
    return _async_client


async def embed_query(text: str) -> list[float]:
    """Embed one user question for retrieval (NIM, query mode)."""
    resp = await _async_embed_client().embeddings.create(
        model=EMBED_MODEL,
        input=[text],
        extra_body={"input_type": "query", "truncate": "END"},
    )
    return list(resp.data[0].embedding)


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document passages for the offline index build (NIM, passage mode)."""
    from openai import OpenAI

    client = OpenAI(base_url=NIM_BASE_URL, api_key=_require_key())
    resp = client.embeddings.create(
        model=EMBED_MODEL,
        input=list(texts),
        extra_body={"input_type": "passage", "truncate": "END"},
    )
    return [list(item.embedding) for item in resp.data]
