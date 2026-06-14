---
title: A renter-rights agent that refuses to answer off-source
summary: A voice agent that answers US renter-rights questions strictly from a prebaked HUD index, names the source out loud, and refuses anything the documents do not cover, plus the fallback gotcha that silently breaks a vector index.
author: Mahimai
---

## The problem

Renters call legal-aid lines with the same questions over and over:
deposits, repairs, landlord entry, fair housing, the basics of eviction.
A first-line helper could answer the grounded ones and free a caseworker
for the rest. The catch is that a wrong answer on housing law is worse
than no answer. So the agent answers United States renter-rights questions
strictly from a prebaked index of public-domain HUD guidance, names the
source out loud, and refuses or redirects to legal aid when a question
goes past the documents or needs facts about a specific lease or state.
It is aimed at housing nonprofits and legal-aid teams that want a
grounded, safe first-line helper for renters.

## Why this stack

The whole stack runs on one key. NVIDIA Riva handles STT and TTS (voice
Magpie-Multilingual.EN-US.Leo), NVIDIA NIM runs the LLM
(meta/llama-3.3-70b-instruct) through its OpenAI-compatible base_url, and
NIM embeddings (nvidia/nv-embedqa-e5-v5) drive retrieval. One
NVIDIA_API_KEY covers STT, LLM, TTS, and embeddings together. A mid
instruct model is enough because answers are short and grounded; the code
notes you can drop to meta/llama-3.1-8b-instruct if it feels laggy. When
no NVIDIA key is present, the stack falls back to OpenAI (Whisper,
gpt-4o-mini, OpenAI TTS, text-embedding-3-small). Each embedding model
carries its own cosine floor (0.40 for NVIDIA, 0.30 for OpenAI) because
the coverage floor is model dependent.

## The interesting part

Retrieval runs on every user turn and injects the matched passages, or an
explicit refusal note, into the turn context *before* the model replies.
Grounding is enforced by injection, not by a tool the model may or may not
call:

```python
        result = retrieve(self._index, query_vec, k=3, floor=self._floor)

        if result.covered:
            passages = "\n\n".join(
                f"[Source: {hit.source_label}]\n{hit.text}" for hit in result.hits
            )
            turn_ctx.add_message(
                role="assistant",
                content=(
                    "System note: answer the user's next message using only the "
                    "source passages below. Name the source out loud. If a specific "
                    "number, deadline, dollar amount, or citation is not present in "
                    "these passages, say you do not have it. If the passages do not "
                    "actually address the question, say so and point the user to "
                    "legal help.\n\n" + passages
                ),
            )
```

The embedding-failure branch had to forbid answering from general
knowledge outright. A soft "try again" note was the one path that could
still leak an ungrounded answer.

## What surprised me

An NVIDIA-to-OpenAI fallback silently breaks the vector index unless the
two are coupled. The index is built offline by one embedding model, so if
the runtime keys later select a different provider, the query vectors live
in a different space and cosine retrieval is garbage. The fix records the
embedding model id inside index.npz, drives both the voice stack and the
embedding backend off the identical NVIDIA-first env priority so they can
never diverge, gives each model its own cosine floor, and makes prewarm
refuse to start on a mismatch. A missing recorded model counts as a
mismatch too, so a stale index forces a rebuild instead of returning junk.

## Run it

Talk to it at https://playground.mahimai.ca/demos/tenant-rights. Or fork
the cookbook, build the index once, and run the worker locally.
