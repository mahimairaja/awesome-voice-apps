"""Maintainer-owned adapter for the standalone tenant rights demo.

The contributed demo runs on NVIDIA (Riva, NIM LLM, NIM embeddings). The
playground only meters Deepgram, OpenAI and Cartesia, so the hosted copy keeps
the demo's agent, prompts, retrieval and UI events and swaps two things:
HostedGuard sets the voice stack, and this module embeds with OpenAI instead of
NIM. The demo's NVIDIA and turn-detector imports are never used here, so they
are satisfied with placeholders while its files load instead of being installed.
"""

import importlib.util
import logging
import sys
import types
from contextvars import ContextVar
from decimal import Decimal
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

_demo = Path(__file__).resolve().parents[1] / "demos" / "tenant-rights"
_UNUSED = ("livekit.plugins.nvidia", "livekit.plugins.turn_detector")


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve their module through sys.modules; the names are unique.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_demo() -> tuple[types.ModuleType, types.ModuleType, types.ModuleType]:
    # The demo imports `rag` by name; give it the demo's own copy only while it
    # loads, so no other demo ever resolves `rag` or the placeholders.
    rag = _load("playground_tenant_rag", _demo / "rag.py")
    placeholders = {name: types.ModuleType(name) for name in _UNUSED if name not in sys.modules}
    if "livekit.plugins.turn_detector" in placeholders:
        multilingual = types.ModuleType("livekit.plugins.turn_detector.multilingual")
        multilingual.MultilingualModel = None
        placeholders["livekit.plugins.turn_detector.multilingual"] = multilingual
    previous = sys.modules.get("rag")
    sys.modules.update(placeholders, rag=rag)
    try:
        agent = _load("playground_tenant_agent", _demo / "agent.py")
        build = _load("playground_tenant_build", _demo / "build_index.py")
    finally:
        for name in placeholders:
            sys.modules.pop(name, None)
        if previous is None:
            sys.modules.pop("rag", None)
        else:
            sys.modules["rag"] = previous
    return rag, agent, build


_rag, _module, _build = _load_demo()

EMBED_MODEL = "text-embedding-3-small"
# Published price for text-embedding-3-small: $0.02 per 1M input tokens.
EMBED_USD_PER_TOKEN = Decimal("0.02") / 1_000_000
# Cosine floor for text-embedding-3-small on this guidance, measured on
# 2026-10-08: renter questions scored 0.32 to 0.56 and greetings or off-topic
# chatter 0.10 to 0.17. A miss is low stakes: the agent answers generally and
# hides the source card instead of citing a section.
FLOOR = 0.25

# Thread jobs share this module but each runs its own event loop, so clients
# stay per call and the hosted agent is found through the running turn.
current_guide: ContextVar = ContextVar("tenant_guide", default=None)
_index: dict | None = None


async def embed(client, texts: list[str]) -> tuple[list[list[float]], int]:
    resp = await client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [list(item.embedding) for item in resp.data], resp.usage.total_tokens


async def embed_query(text: str) -> list[float]:
    guide = current_guide.get()
    if guide is None:
        raise RuntimeError("Tenant rights turn has no hosted agent")
    return await guide.embed_question(text)


# RentersGuide looks this name up at call time.
_module.embed_query = embed_query


def _chunks() -> tuple[list[str], list[str]]:
    texts, labels = [], []
    for path, label in _build.SOURCES:
        for heading, body in _build.chunk_markdown(path.read_text(encoding="utf-8")):
            if heading not in _build.SKIP_HEADINGS:
                texts.append(f"{heading}\n{body}")
                labels.append(label)
    return texts, labels


def _unindexed() -> dict:
    texts, labels = _chunks()
    return {"vectors": np.zeros((0, 1), np.float32), "texts": texts, "labels": labels}


async def tenant_index(client) -> dict:
    """Embed the guidance once per worker and reuse it for every call.

    The index is about 2,000 tokens (under a hundredth of a cent), so the
    worker builds it on the first call instead of shipping a generated file.
    Two first calls may both build it; the arrays are read-only once stored.
    If OpenAI is unreachable the call still runs with the topic list and no
    citations, and the next call tries again.
    """
    global _index
    if _index is not None:
        return _index
    index = _unindexed()
    try:
        vectors, _ = await embed(client, index["texts"])
    except Exception:
        logger.warning("Tenant rights index unavailable; answering without sources")
        return index
    index["vectors"] = np.asarray(vectors, dtype=np.float32)
    index["model"] = EMBED_MODEL
    _index = index
    return _index


class TenantGuide(_module.RentersGuide):
    """The contributed RentersGuide, embedding through a per-call OpenAI client."""

    def __init__(self, room) -> None:
        super().__init__(room, _unindexed(), FLOOR)
        from openai import AsyncOpenAI

        self._embeddings = AsyncOpenAI(max_retries=0, timeout=5)

    def meter_embedding(self, tokens: int) -> None:
        """Bill question embeddings to this call; the hosted class overrides it."""

    async def load_index(self) -> None:
        self._index = await tenant_index(self._embeddings)

    async def embed_question(self, text: str) -> list[float]:
        vectors, tokens = await embed(self._embeddings, [text])
        self.meter_embedding(tokens)
        return vectors[0]

    async def on_exit(self) -> None:
        await self._embeddings.close()

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        token = current_guide.set(self)
        try:
            await super().on_user_turn_completed(turn_ctx, new_message)
        finally:
            current_guide.reset(token)


GREETING = (
    "Greet the user in one short, friendly sentence. Tell them the topics "
    "you can cover are listed on screen and they can ask about any of them. "
    "Do not mention legal advice; an on-screen notice already covers it."
)


def publish_tenant(room, index: dict) -> None:
    _module._publish_static_ui(room, index)
