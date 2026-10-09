"""Offline tests for the copilot: a fake OpenAI client, no network."""

import asyncio
import json
from types import SimpleNamespace

import copilot
import pytest
from copilot import BOOKED_ASK, CLOSE_ASK, DISCOVERY, FLOOR, PLAYBOOK, Copilot

IDS = [card.id for card in PLAYBOOK]


def vector(text: str) -> list[float]:
    """One axis per card. Tagged lines and a card's own phrases sit on its axis."""
    for i, card in enumerate(PLAYBOOK):
        if text.startswith(f"[{card.id}]") or text in (card.title, *card.triggers):
            return [1.0 if j == i else 0.0 for j in range(len(PLAYBOOK))]
    # Off-topic: equally far from every card, below the floor.
    return [1.0] * len(PLAYBOOK)


class FakeClient:
    def __init__(self, reply=None, fail_line=False, fail_embed=False, delay=0.0):
        self.reply = reply or {"say": "Ask how they match freight invoices."}
        self.fail_line = fail_line
        self.fail_embed = fail_embed
        self.delay = delay
        self.lines = 0
        self.embeddings = SimpleNamespace(create=self._embed)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._line))

    async def _embed(self, model, input):
        if self.fail_embed:
            raise RuntimeError("offline")
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=vector(text)) for text in input],
            usage=SimpleNamespace(total_tokens=len(input) * 5),
        )

    async def _line(self, **kwargs):
        self.lines += 1
        await asyncio.sleep(self.delay)
        if self.fail_line:
            raise RuntimeError("offline")
        data = {"say": "", **{key: "" for key, _, _ in DISCOVERY}, **self.reply}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))],
            usage=SimpleNamespace(prompt_tokens=300, completion_tokens=30),
        )


@pytest.fixture(autouse=True)
def fresh_index():
    copilot._index = None
    yield
    copilot._index = None


def make(client, **kwargs):
    snapshots, billed = [], []
    pilot = Copilot(
        client=client,
        publish=snapshots.append,
        meter=lambda *args: billed.append(args),
        **kwargs,
    )
    return pilot, snapshots, billed


async def settle(pilot):
    await asyncio.gather(*pilot._tasks)


async def test_card_lands_before_the_line_and_both_are_timed():
    pilot, snapshots, billed = make(FakeClient(delay=0.05))
    pilot.heard_prospect("[quillpay] We already saw a demo from Quillpay.")
    await settle(pilot)
    # The first published cue with a card has no line yet: retrieval never waits.
    first = next(s["cues"][0] for s in snapshots if s["cues"] and s["cues"][0]["card"])
    assert first["card"]["id"] == "quillpay" and first["line"] is None
    cue = snapshots[-1]["cues"][0]
    assert cue["done"] and cue["line"] == "Ask how they match freight invoices."
    assert 0 <= cue["retrieval_ms"] <= cue["line_ms"]
    assert cue["card"]["note"].startswith("They win")
    assert cue["ask"] == PLAYBOOK[IDS.index("quillpay")].ask
    assert snapshots[-1]["latency"]["line"] == cue["line_ms"]
    # The query and the line bill the call; the shared playbook index does not.
    assert [b[0] for b in billed] == ["text-embedding-3-small", "gpt-4o-mini"]
    assert billed[1][1:] == (300, 30)


async def test_small_talk_gets_no_card_but_moves_discovery():
    pilot, snapshots, _ = make(FakeClient())
    pilot.heard_prospect("Hi, this is Dana. I have ten minutes.")
    await settle(pilot)
    cue = snapshots[-1]["cues"][0]
    assert cue["card"] is None and cue["score"] < FLOOR
    assert cue["ask"] == DISCOVERY[0][2]


async def test_discovery_notes_fill_and_the_next_question_advances():
    client = FakeClient(reply={"say": "Dig in.", "pain": "lost email approvals", "impact": "$48k"})
    pilot, snapshots, _ = make(client)
    pilot.heard_prospect("[duplicates] We paid a carrier twice, forty-eight grand.")
    await settle(pilot)
    found = snapshots[-1]["found"]
    assert found["pain"] == "lost email approvals" and found["impact"] == "$48k"
    # The duplicates card covers impact, which is now known: fall back to discovery order.
    assert snapshots[-1]["cues"][0]["ask"] == DISCOVERY[2][2]
    for key, _, _ in DISCOVERY:
        pilot.found[key] = "known"
    assert pilot.next_question(None) == CLOSE_ASK


async def test_the_first_agreed_next_step_is_logged_once():
    client = FakeClient(reply={"say": "Lock it in.", "next_step": "Technical demo, Tuesday"})
    pilot, snapshots, _ = make(client)
    pilot.heard_prospect("Fine, a demo with Priya on Tuesday works.")
    await settle(pilot)
    assert snapshots[-1]["next_step"] == "Technical demo, Tuesday"
    assert snapshots[-1]["cues"][0]["ask"] == BOOKED_ASK
    client.reply = {"say": "Thanks.", "next_step": "Something else"}
    pilot.heard_prospect("And maybe lunch too.")
    await settle(pilot)
    assert snapshots[-1]["next_step"] == "Technical demo, Tuesday"


async def test_failed_generation_keeps_the_card():
    pilot, snapshots, _ = make(FakeClient(fail_line=True))
    pilot.heard_prospect("[price] That is more than we can spend.")
    await settle(pilot)
    cue = snapshots[-1]["cues"][0]
    assert cue["card"]["id"] == "price" and cue["line"] is None and cue["done"]
    assert cue["ask"]


async def test_failed_retrieval_still_writes_a_line():
    pilot, snapshots, _ = make(FakeClient(fail_embed=True))
    pilot.heard_prospect("[security] Our CISO will want a review.")
    await settle(pilot)
    cue = snapshots[-1]["cues"][0]
    assert cue["card"] is None and cue["line"] and cue["done"]
    assert copilot._index is None


async def test_limits_bound_paid_work_and_cues_stay_short():
    client = FakeClient()
    pilot, snapshots, billed = make(client, max_lines=2, max_lookups=3)
    for card in PLAYBOOK[:5]:
        pilot.heard_prospect(f"[{card.id}] {card.triggers[0]}")
        await settle(pilot)
    assert client.lines == 2
    assert sum(1 for b in billed if b[0] == "text-embedding-3-small") == 3
    cues = snapshots[-1]["cues"]
    assert [c["n"] for c in cues] == [5, 4, 3]
    assert cues[0]["card"] is None and cues[0]["line"] is None and cues[0]["done"]


async def test_talk_ratio_next_step_and_close():
    pilot, snapshots, _ = make(FakeClient())
    pilot.heard_rep("How do approvals work today?")
    pilot.heard_rep("   ")
    assert snapshots[-1]["words"] == {"rep": 5, "prospect": 0}
    pilot.heard_prospect("[timing] Year-end close is coming.")
    await pilot.aclose()
    count = len(snapshots)
    pilot.heard_rep("still talking")
    assert pilot.heard_prospect("anything") is None
    assert len(snapshots) == count


async def test_long_lines_are_clipped_for_the_screen():
    pilot, snapshots, _ = make(FakeClient())
    pilot.heard_prospect("word " * 200)
    await settle(pilot)
    assert len(snapshots[-1]["cues"][0]["heard"]) <= copilot.MAX_TEXT
