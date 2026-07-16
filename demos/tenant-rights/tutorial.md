---
title: How to build a renter-rights voice agent grounded in real docs
summary: Build a US renter-rights voice agent that answers from a prebaked HUD index, shows its source, and refuses to invent numbers, from an empty folder to a running worker, on NVIDIA Riva and NIM.
author: Mahimai
github: mahimairaja
---

## What you will build

A voice assistant for US renter rights. It answers from a prebaked index of
public HUD guidance, shows the source it is reading on screen, and refuses to
state an exact number that is not in the documents. The finished demo is
`tenant-rights`.

The whole stack runs on one NVIDIA key (Riva STT and TTS, a NIM LLM, NIM
embeddings) plus three LiveKit values. And `uv`.

## 1. Scaffold

Pin Python to `3.11`. `pyproject.toml` pulls the NVIDIA plugins, plus the openai
plugin as NIM's transport and numpy for the vector store:

```toml
dependencies = [
    "livekit-agents[nvidia,openai,silero,turn-detector]>=1.5,<2.0",
    "python-dotenv>=1.0",
    "numpy>=1.26",
]
```

`.env.example` is four lines: `NVIDIA_API_KEY` and the three LiveKit keys.

> [!IMPORTANT]
> Build the retrieval index once before running: `uv run python build_index.py`,
> then `uv run python agent.py download-files`.

## 2. One key, the whole stack

NVIDIA has no native LiveKit LLM plugin, so the NIM model is reached with the
openai plugin pointed at NIM's OpenAI-compatible endpoint. STT and TTS are Riva:

```python
def build_voice_stack():
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        raise RuntimeError("NVIDIA_API_KEY is not set; the tenant-rights stack needs it.")
    return (
        nvidia.STT(language_code="en-US"),
        openai.LLM(model=NIM_LLM_MODEL, base_url=NIM_BASE_URL, api_key=api_key),
        nvidia.TTS(voice="Magpie-Multilingual.EN-US.Leo", language_code="en-US"),
    )
```

## 3. Grounding by injection

Grounding is not optional, so it does not ride on a tool the model might skip.
The agent overrides `on_user_turn_completed`: it embeds the question, retrieves
the top passages with a coverage floor, and when they cover the question, injects
them as a system note the model must lean on, then shows the source on a Card:

```python
result = retrieve(self._index, query_vec, k=3, floor=self._floor)

if result.covered:
    passages = "\n\n".join(
        f"[Source: {hit.source_label}]\n{hit.text}" for hit in result.hits
    )
    turn_ctx.add_message(
        role="assistant",
        content=(
            "System note: answer the user's next message helpfully in one "
            "or two sentences. Use the source passages below as your main "
            "grounding; you may add well-established general US "
            "renter-rights knowledge. When a specific number or rule varies "
            "by state, give the common rule and note it can vary, rather "
            "than deflecting. Do not state an exact number, deadline, or "
            "citation as certain unless it is in these passages. The screen "
            "shows the source, so you need not name it.\n\n" + passages
        ),
    )
```

The wording is load-bearing: lean on the passages, allow common-rule general
knowledge, but never assert an exact figure that is not grounded.

## 4. The fallbacks

Three guards keep it safe. An empty or garbled turn clears the card and raises
`StopResponse`, so the framework never answers with no grounding:

```python
question = (new_message.text_content or "").strip()
if not question:
    _unmount_card(self._room)
    raise StopResponse()
```

If the embedding lookup itself fails, the agent injects a note telling the model
not to answer from general knowledge and to ask the user to try again. And when
retrieval returns nothing above the floor, it injects a note to answer briefly
from common knowledge or redirect, and unmounts the source card so the screen
never shows a stale citation.

## 5. Standing UI

Two panels mount once and never update: a legal notice Card ("information, not
legal advice") and a topics List derived from the index section headings, so the
menu always reflects exactly what the agent can answer.

> [!WARNING]
> A prebaked vector index is welded to the embedding model that built it. Embed
> queries with a different model and cosine retrieval returns confident garbage,
> never an error. `prewarm` stamps the model id into the index and refuses to
> start on a mismatch, forcing a rebuild instead of serving junk.

## 6. The eval

The behavioral test stubs `embed_query` and `retrieve` with one canned HUD
passage, so it exercises the grounded-answer behavior rather than the vector
store, then asks a deposit question against the real NIM LLM and asserts the
task-completion, relevancy, and coherence judges pass:

```python
monkeypatch.setattr(tenant, "embed_query", _fake_embed_query)
monkeypatch.setattr(tenant, "retrieve", _fake_retrieve)
...
await session.run(user_input="How long does my landlord have to return my deposit?")
```

## 7. Run it

```sh
cp .env.example .env
```

```sh
uv sync
```

```sh
uv run python build_index.py
```

```sh
uv run python agent.py download-files
```

```sh
uv run python agent.py dev
```

Open https://playground.mahimai.ca/demos/tenant-rights and ask about a security
deposit, then ask something the HUD docs do not cover and watch it point you to
legal aid instead of guessing.
