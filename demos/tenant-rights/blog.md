---
title: How to build a renter-rights voice agent grounded in real docs
summary: A US renter-rights voice agent that answers from a prebaked index of public HUD guidance, shows the source on screen, and refuses to invent numbers, on NVIDIA Riva and NIM.
author: Mahimai
github: mahimairaja
---

A renter-rights helper has to be right: telling someone they have thirty days
when they have three is not a small mistake. So this agent answers from a
prebaked index of public HUD guidance, shows the source it is reading, and will
not state an exact number that is not in the documents.

The whole stack runs on one NVIDIA key: Riva STT, a NIM LLM
(`meta/llama-3.3-70b-instruct`), Riva TTS, and NIM embeddings. NVIDIA has no
native LiveKit LLM plugin, so the NIM model is reached through the openai plugin
pointed at the NIM endpoint. The openai client here is just NVIDIA's transport:

```python
openai.LLM(model=NIM_LLM_MODEL, base_url=NIM_BASE_URL, api_key=api_key)
```

Grounding is not optional, so it does not ride on a tool the model might skip.
On every turn, `on_user_turn_completed` embeds the question and retrieves the top
passages:

```python
result = retrieve(self._index, query_vec, k=3, floor=self._floor)
if result.covered:
    passages = "\n\n".join(
        f"[Source: {hit.source_label}]\n{hit.text}" for hit in result.hits
    )
```

When they cover the question, the agent adds them to the turn context as a system
note that tells the model to lean on the passages, note when a rule varies by
state, and never state an exact number that is not in them. The top source shows
on a Card. The safety is in the guards: an empty or garbled turn clears the card
and raises `StopResponse` so the model never answers with no grounding, and a
failed embedding lookup tells the model in-context not to answer from general
knowledge at all.

```python
question = (new_message.text_content or "").strip()
if not question:
    _unmount_card(self._room)
    raise StopResponse()
```

> [!IMPORTANT]
> The index is welded to the embedding model that built it. `prewarm` stamps the
> model id into the index and refuses to start on a mismatch, so build it once
> with `uv run python build_index.py` before `dev`, and rebuild if you change
> embeddings.

Build it from an empty folder in the full walkthrough, or talk to the finished
agent at https://playground.mahimai.ca/demos/tenant-rights.
