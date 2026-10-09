"""Check the retrieval floor against lines a prospect might say.

Run after editing the playbook: `uv run python measure_floor.py`. It embeds the
playbook, scores each sample line, and prints the best card and its score, so
you can see where on-topic lines and small talk separate. Costs a fraction of
a cent on OpenAI.
"""

import asyncio

from copilot import FLOOR, best_card, playbook_index
from dotenv import load_dotenv
from openai import AsyncOpenAI

# (expected card id or None, a line the prospect might say)
SAMPLES = [
    ("price", "Honestly the last quote we got for AP software was way over what I can spend."),
    ("price", "What's this going to run us per year?"),
    ("timing", "We're heading into year-end close, so this really isn't the moment."),
    ("status-quo", "My three clerks get through it fine. I'm not sure what's broken."),
    ("integration", "We're on NetSuite and IT is buried. Is this going to be a project?"),
    ("security", "Our CISO is going to ask where vendor banking data lives."),
    ("adoption", "My approvers barely log into NetSuite. They won't learn another tool."),
    ("brush-off", "Look, can you just send me something to read?"),
    ("quillpay", "To be upfront, we've already seen a demo from Quillpay."),
    ("quillpay", "Why would I pick you over Quillpay? They were cheaper."),
    ("tallyforge", "Our sister company runs on Tallyforge and seems happy."),
    ("duplicates", "Last quarter we paid one carrier's invoice twice. Forty-eight grand."),
    ("close", "Close takes us nine days and half of that is chasing sign-offs."),
    ("buying-group", "Anything over fifty thousand goes to our CFO, Mark."),
    (None, "Hi, this is Dana from Kestrel Freight. I've got about ten minutes."),
    (None, "Sure, go ahead."),
    (None, "We move freight across Ontario and the Midwest."),
    (None, "Okay, that makes sense. Thanks."),
    (None, "Sorry, could you repeat that?"),
]


async def main() -> None:
    load_dotenv()
    client = AsyncOpenAI()
    index = await playbook_index(client)
    resp = await client.embeddings.create(
        model="text-embedding-3-small", input=[line for _, line in SAMPLES]
    )
    misses = 0
    for (expected, line), item in zip(SAMPLES, resp.data):
        card, score = best_card(index, item.embedding)
        got = card.id if card else None
        ok = got == expected
        misses += not ok
        print(f"{'ok ' if ok else 'MISS'} {score:.3f} {str(got):13} {line}")
    print(f"floor {FLOOR}: {misses} misses of {len(SAMPLES)}")
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
