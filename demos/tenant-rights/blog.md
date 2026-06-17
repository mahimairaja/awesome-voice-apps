---
title: A renter-rights agent that refuses to answer off-source
summary: A voice agent that answers US renter-rights questions strictly from a prebaked HUD index, names the source out loud, and refuses anything the documents do not cover, plus the gotcha that silently binds a vector index to the model that built it.
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
(meta/llama-3.3-70b-instruct) over its OpenAI-compatible endpoint, and NIM
embeddings (nvidia/nv-embedqa-e5-v5) drive retrieval. One NVIDIA_API_KEY
covers STT, LLM, TTS, and embeddings together; the openai client is just
NVIDIA's transport, since NIM speaks the OpenAI protocol and LiveKit has no
native NVIDIA LLM plugin. A mid instruct model is enough because answers
are short and grounded; the code notes you can drop to
meta/llama-3.1-8b-instruct if it feels laggy. The coverage floor (cosine
0.40) is tuned to nv-embedqa-e5-v5, because how high a real match scores is
embedding-model dependent.

## The interesting part

Retrieval runs on every user turn. When a passage matches, it is injected
into the turn context *before* the model replies; when nothing matches, a
note tells the model to stay conversational and never answer a renter-rights
question from general knowledge. Grounding is enforced by injection, not by a
tool the model may or may not call:

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
                    "source passages below. Open by naming the source, for example "
                    '"According to HUD\'s resident rights guidance," then give the '
                    "single most useful point in one or two sentences and offer to "
                    "go deeper. Do not recite every passage. If a specific number, "
                    "deadline, dollar amount, or citation is not in these passages, "
                    "say you do not have it. If the passages do not actually answer "
                    "the question, say in one sentence that you do not have that "
                    "detail.\n\n" + passages
                ),
            )
```

The embedding-failure branch had to forbid answering from general
knowledge outright. A soft "try again" note was the one path that could
still leak an ungrounded answer.

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
