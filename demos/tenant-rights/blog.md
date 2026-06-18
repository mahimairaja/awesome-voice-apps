---
title: A renter-rights agent grounded in HUD guidance
summary: A voice agent that answers US renter-rights questions strictly from a prebaked HUD index, names its source out loud, and refuses anything the documents don't cover. Plus the trap where a vector index is silently welded to the embedding model that built it.
author: Mahimai
github: mahimairaja
---

## The problem

Renters call legal-aid lines with the same questions over and over: deposits, repairs, landlord entry, fair housing, the basics of eviction. A first-line helper could answer the grounded ones and free a caseworker for the harder cases.

The catch is that a wrong answer on housing law is worse than no answer. Telling someone they have 30 days when they have 3 is not a small mistake. So this agent answers only from a prebaked index of public-domain HUD guidance, names its source out loud, and refuses, or hands off to legal aid, the moment a question goes past the documents or turns on a specific lease or state.

The index is built from HUD's public renter-rights material: its [Tenant Rights](https://www.hud.gov/topics/rental_assistance/tenantrights) page and the [Fair Housing Act rights](https://www.hud.gov/program_offices/fair_housing_equal_opp/fair_housing_rights_and_obligations) it enforces. That is the entire knowledge base, which is exactly why the agent can name where every answer comes from.

## Why this stack

The whole stack runs on one key. That is the unusual part: STT, LLM, TTS, and embeddings all come from NVIDIA, so one `NVIDIA_API_KEY` covers the entire pipeline. Riva handles speech in and out (voice Magpie-Multilingual.EN-US.Leo), NIM runs the LLM (`meta/llama-3.3-70b-instruct`) over its OpenAI-compatible endpoint, and NIM embeddings (`nvidia/nv-embedqa-e5-v5`) drive retrieval. The `openai` client here is just NVIDIA's transport. NIM speaks the OpenAI protocol, and LiveKit has no native NVIDIA LLM plugin, so you point the OpenAI client at NIM's endpoint and it works.

A mid-size instruct model is plenty, because grounded answers are short; if it feels laggy you can drop to `meta/llama-3.1-8b-instruct` with no other changes. One number is worth calling out: the coverage floor (cosine 0.40) is tuned to `nv-embedqa-e5-v5` specifically. How high a real match scores is a property of the embedding model, not a universal threshold, so swap the embedding model and that number is wrong.

## The interesting part

Grounding is the whole game here, so it cannot be optional. The agent does not _offer_ the model a search tool and hope it calls it. On every user turn, retrieval runs first, and the matched passages, or an explicit refusal note, are injected into the turn context before the model is allowed to reply:

![Grounding by injection in the tenant-rights agent](https://assets.mahimai.ca/tenant-rights-grounding-blog.svg)

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

The wording of that system note is load-bearing. It has to forbid answering from memory, demand the source be named aloud, and treat a missing number as "I don't have it" rather than an invitation to guess. The hardest case was the embedding-failure branch: when retrieval itself fails, the agent has to refuse outright. A softer "let me try again" was the one remaining path that could still leak an answer the documents never supported.

## What surprised me

A prebaked vector index is silently bound to the embedding model that built it. The index is embedded offline, so if the runtime ever embeds queries with a different model, the query vectors land in a different space and cosine retrieval returns confident garbage, never an error.

The fix stamps the embedding model id inside `index.npz`, and prewarm refuses to start if the recorded id does not match the model the current keys resolve to. A missing id counts as a mismatch, so a stale index forces a rebuild instead of serving junk. I hit this exactly once, swapping the embedding model under an index that was already built, and once was enough.

## Run it

Talk to it at [playground.mahimai.ca/demos/tenant-rights](https://playground.mahimai.ca/demos/tenant-rights): paste your NVIDIA key, connect, and ask about a security deposit. Or fork the cookbook, build the index once with the prebake script, and run the worker locally.

To see the refusal work, ask it something it cannot ground: a question about your specific lease, or the law in a state the HUD docs do not detail. It will tell you it does not have that and point you to legal aid, instead of guessing.
