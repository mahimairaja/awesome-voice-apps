---
title: A renter-rights voice agent grounded in HUD guidance
summary: A voice agent that answers US renter-rights questions, grounds them in public HUD guidance it shows on screen, and stays honest about what varies state by state, plus the gotcha that silently binds a vector index to the model that built it.
author: Mahimai
---

## The problem

Renters ask the same handful of questions over and over: deposits,
repairs, landlord entry, fair housing, the basics of eviction. A
first-line voice helper can answer them and free a caseworker for the
rest. The hard part is staying useful without overstepping. HUD's public
guidance covers the general rights well, but the specifics (exact notice
periods, deposit caps, deadlines) vary by state and by lease. So the agent
grounds its answers in a prebaked index of public-domain HUD guidance,
shows the exact section it is drawing from on screen, and when a detail
comes down to state law it gives the common rule and says so, instead of
deflecting. It is aimed at housing nonprofits and legal-aid teams that want
a grounded, honest first-line helper for renters.

## Why this stack

The whole stack runs on one key. NVIDIA Riva handles STT and TTS (voice
Magpie-Multilingual.EN-US.Leo), NVIDIA NIM runs the LLM
(meta/llama-3.3-70b-instruct) over its OpenAI-compatible endpoint, and NIM
embeddings (nvidia/nv-embedqa-e5-v5) drive retrieval. One NVIDIA_API_KEY
covers STT, LLM, TTS, and embeddings together; the openai client is just
NVIDIA's transport, since NIM speaks the OpenAI protocol and LiveKit has no
native NVIDIA LLM plugin. A mid instruct model is enough because answers
are short; the code notes you can drop to meta/llama-3.1-8b-instruct if it
feels laggy. The coverage floor (cosine 0.33) is tuned to nv-embedqa-e5-v5:
real renter-rights questions score around 0.38 and up on this model, while
greetings and off-topic chatter sit lower, so the floor decides what earns
a cited passage.

## The interesting part

Retrieval runs on every user turn. When a passage matches, it is injected
into the turn context *before* the model replies and drives the on-screen
citation. The model leans on that passage and may add well-established
general knowledge, but it is told to flag anything that varies by state and
never to assert an exact statute or figure that is not in the source.
Grounding is injected, not left to a tool the model may or may not call:

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

An earlier version refused everything off-source, and it felt useless: it
deflected real questions to "that depends on state law." The fix was to
keep retrieval as grounding and citation while letting the agent answer
like a knowledgeable person, honest about what it cannot pin down. One
branch stays strict, though: when the embedding call itself fails, the
agent says it could not look that up and asks the user to retry, rather
than guessing while the grounding path is down.

## What surprised me

A prebaked vector index is silently bound to the embedding model that built
it. The index is embedded offline, so if the runtime ever embeds queries
with a different model, the query vectors land in a different space and
cosine retrieval returns confident garbage, never an error. The fix stamps
the embedding model id inside index.npz and makes prewarm refuse to start
when the recorded model does not match the one the current keys select. A
missing id counts as a mismatch too, so a stale index forces a rebuild
instead of serving junk. This first bit me swapping the embedding model out
from under an index that was already built, which is exactly the trap.

## Run it

Talk to it at https://playground.mahimai.ca/demos/tenant-rights. Or fork
the cookbook, build the index once, and run the worker locally.
