"""Offline index builder for the tenant-rights demo.

Run once, with NVIDIA_API_KEY set, before the demo can answer:

    uv run --no-project python build_index.py

It reads each source markdown file, splits it into one chunk per section
heading, embeds the chunks via NVIDIA NIM (passage mode), and writes
data/index.npz. It is never imported at runtime; agent.py only reads the
index. To add the HUD Fair Housing document later, drop the markdown in data/,
add a line to SOURCES, and rerun.

texts and labels are saved as native numpy unicode arrays (not object arrays),
so the runtime loads the index with allow_pickle=False.
"""

from __future__ import annotations

import pathlib

import numpy as np
from dotenv import load_dotenv

from rag import EMBED_MODEL, embed_documents

load_dotenv()

HERE = pathlib.Path(__file__).parent
DATA = HERE / "data"

# Each source file maps to the label the agent says out loud when it cites it.
SOURCES = [
    (
        DATA / "hud-resident-rights.md",
        "HUD's resident rights and responsibilities guidance",
    ),
]

# Section headings that are guidance metadata, not answerable content. Their text
# is already hard-coded in the agent's refuse-and-redirect path, so keeping them
# out of the index stops an out-of-scope question from matching them above the
# floor and being answered instead of cleanly redirected.
SKIP_HEADINGS = {"Where to get help"}


def chunk_markdown(text: str) -> list[tuple[str, str]]:
    """Split on level-2 headings; each section becomes one chunk.

    Content before the first level-2 heading (the title and the source note) is
    intentionally skipped: it is metadata, not answerable guidance.
    """
    chunks: list[tuple[str, str]] = []
    heading: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                chunks.append((heading, "\n".join(body).strip()))
            heading = line[3:].strip()
            body = []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        chunks.append((heading, "\n".join(body).strip()))
    return [(h, b) for h, b in chunks if b]


def main() -> None:
    texts: list[str] = []
    labels: list[str] = []
    for path, label in SOURCES:
        markdown = path.read_text(encoding="utf-8")
        for heading, body in chunk_markdown(markdown):
            if heading in SKIP_HEADINGS:
                continue
            texts.append(f"{heading}\n{body}")
            labels.append(label)

    if not texts:
        raise SystemExit("no chunks found; check the source markdown")

    print(f"embedding {len(texts)} chunks via {EMBED_MODEL} ...")
    vectors = np.asarray(embed_documents(texts), dtype=np.float32)

    out = DATA / "index.npz"
    np.savez_compressed(
        out,
        vectors=vectors,
        texts=np.array(texts),
        labels=np.array(labels),
    )
    print(f"wrote {out} ({len(texts)} chunks, dim {vectors.shape[1]})")


if __name__ == "__main__":
    main()
