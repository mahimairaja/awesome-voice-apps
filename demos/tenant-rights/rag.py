"""Portable retrieval core for the tenant-rights demo.

No LiveKit imports. `retrieve` is pure numpy and is the unit-tested core. The
embedding helpers call NVIDIA NIM over its OpenAI-compatible endpoint and lazily
import the openai client, so this module imports cleanly without the openai
package installed (which is what lets test_rag.py run without the voice stack).

The same `retrieve`, `embed_query`, and `embedding_backend` lift into any other
runtime unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

# NVIDIA NIM: one NVIDIA_API_KEY covers STT, LLM, TTS, and embeddings. Embeddings
# go over NIM's OpenAI-compatible endpoint, so the openai client is the transport.
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NIM_EMBED_MODEL = "nvidia/nv-embedqa-e5-v5"

# Default cosine floor used by retrieve() when a caller passes none. The real
# per-run floor comes from the EmbedBackend, since the floor is embedding-model
# dependent.
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


@dataclass
class EmbedBackend:
    """The NVIDIA NIM embedding backend the runtime uses.

    agent.py builds its voice stack from the same NVIDIA_API_KEY, so embeddings
    and the STT/LLM/TTS providers always match.
    """

    provider: str  # always "nvidia"
    model: str
    base_url: str  # the NIM endpoint
    api_key_env: str
    query_passage: bool  # nv-embedqa needs an input_type hint
    floor: float  # cosine floor tuned to this model


def embedding_backend() -> EmbedBackend:
    """The NVIDIA NIM embedding backend. Requires NVIDIA_API_KEY."""
    if not os.environ.get("NVIDIA_API_KEY"):
        raise RuntimeError(
            "NVIDIA_API_KEY is not set; the tenant-rights stack needs it."
        )
    # Floor 0.33 is tuned to nv-embedqa-e5-v5: on this model real renter-rights
    # questions score ~0.38 and up, while greetings and off-topic chatter top out
    # near 0.25, so 0.33 admits the former and rejects the latter with margin.
    return EmbedBackend(
        "nvidia", NIM_EMBED_MODEL, NIM_BASE_URL, "NVIDIA_API_KEY", True, 0.33
    )


def load_index(path: str | os.PathLike) -> dict:
    """Load a prebaked index written by build_index.py.

    build_index.py stores texts, labels, and the embedding model id as native
    numpy unicode arrays (not object arrays), so we load with allow_pickle=False.
    No pickle is involved, which keeps the load free of any arbitrary-code
    execution surface.
    """
    data = np.load(path, allow_pickle=False)
    model = str(data["model"]) if "model" in data else ""
    return {
        "vectors": np.asarray(data["vectors"], dtype=np.float32),
        "texts": [str(t) for t in data["texts"]],
        "labels": [str(label) for label in data["labels"]],
        "model": model,
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


def _api_key(backend: EmbedBackend) -> str:
    key = os.environ.get(backend.api_key_env)
    if not key:
        raise RuntimeError(
            f"{backend.api_key_env} is not set; cannot reach the embedding provider."
        )
    return key


_async_client = None


def _async_embed_client(backend: EmbedBackend):
    """Build the embeddings client once and reuse it across turns.

    Reusing one AsyncOpenAI keeps its keep-alive connection pool warm, so each
    turn skips a fresh TLS handshake and nothing is left dangling for the GC.
    The openai import stays lazy so this module imports without the package.
    """
    global _async_client
    if _async_client is None:
        from openai import AsyncOpenAI

        _async_client = AsyncOpenAI(
            base_url=backend.base_url, api_key=_api_key(backend)
        )
    return _async_client


async def embed_query(text: str) -> list[float]:
    """Embed one user question for retrieval via NVIDIA NIM."""
    backend = embedding_backend()
    kwargs: dict = {"model": backend.model, "input": [text]}
    if backend.query_passage:
        kwargs["extra_body"] = {"input_type": "query", "truncate": "END"}
    resp = await _async_embed_client(backend).embeddings.create(**kwargs)
    return list(resp.data[0].embedding)


def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed document passages for the offline index build via NVIDIA NIM."""
    backend = embedding_backend()
    from openai import OpenAI

    client = OpenAI(base_url=backend.base_url, api_key=_api_key(backend))
    kwargs: dict = {"model": backend.model, "input": list(texts)}
    if backend.query_passage:
        kwargs["extra_body"] = {"input_type": "passage", "truncate": "END"}
    resp = client.embeddings.create(**kwargs)
    return [list(item.embedding) for item in resp.data]
