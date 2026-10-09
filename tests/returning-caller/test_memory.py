import memory
import pytest

NOW = 1_760_000_000


def consented(now=NOW):
    return memory.add([], "consent", "Keep notes between calls.", "Said yes.", now)


def test_nothing_is_kept_without_consent():
    with pytest.raises(ValueError, match="no consent"):
        memory.add([], "preference", "Prefers a text over a call.", "So we text.", NOW)
    entries = consented()
    entries = memory.add(entries, "preference", "Prefers a text over a call.", "So we text.", NOW)
    assert [e["kind"] for e in entries] == ["consent", "preference"]
    # Consent is idempotent: a second yes keeps the first record.
    assert memory.add(entries, "consent", "Again.", "Again.", NOW + 5) == entries


def test_expiry_follows_the_kind_not_the_model():
    entries = consented()
    for kind in ("preference", "task", "context"):
        entries = memory.add(entries, kind, f"A {kind} note.", "Why.", NOW)
    expiry = {e["kind"]: e["expires"] - e["saved"] for e in entries}
    assert expiry == {
        "consent": 30 * memory.DAY,
        "preference": 30 * memory.DAY,
        "task": 7 * memory.DAY,
        "context": 3 * memory.DAY,
    }
    with pytest.raises(ValueError, match="unknown kind"):
        memory.add(entries, "forever", "x", "y", NOW)


def test_expired_notes_become_tombstones():
    entries = memory.add(consented(), "context", "Flying to Lisbon on the 20th.", "Card.", NOW)
    later = NOW + 3 * memory.DAY + 1
    assert memory.notes(entries, later) == []
    swept = memory.expire(entries, later)
    trip = swept[-1]
    assert trip["text"] is None and trip["why"] is None
    assert trip["by"] == "expiry" and trip["forgotten"] == NOW + 3 * memory.DAY
    assert memory.consent(swept, later)


@pytest.mark.parametrize(
    "note, reason",
    [
        ("Card number is 4520 1234 5678 9012.", "account, card or phone numbers"),
        ("Account 004-12345-678 is the joint one.", "account, card or phone numbers"),
        ("Call back on 416 555 0199.", "account, card or phone numbers"),
        ("Their PIN is the usual one.", "credentials"),
        ("Password hint is the dog's name.", "credentials"),
        ("SIN is on the form.", "government ID numbers"),
    ],
)
def test_sensitive_notes_are_never_kept(note, reason):
    assert memory.screen(note) == reason
    with pytest.raises(ValueError, match="never kept"):
        memory.add(consented(), "context", note, "x", NOW)


def test_ordinary_notes_with_small_numbers_pass():
    for note in (
        "Wire of 40,000 to the university is pending.",
        "Flying to Lisbon on October 20.",
        "Meets their advisor Thursday at 2.",
    ):
        assert memory.screen(note) is None, note


def test_forget_one_and_withdraw_consent():
    entries = consented()
    entries = memory.add(entries, "preference", "Prefers texts.", "Why.", NOW)
    entries = memory.add(entries, "task", "Owed a call about the wire.", "Why.", NOW)
    pref = entries[1]["id"]
    entries = memory.forget(entries, pref, NOW + 10, "caller")
    assert [e["kind"] for e in memory.notes(entries, NOW + 10)] == ["task"]
    assert entries[1]["text"] is None and entries[1]["by"] == "caller"
    with pytest.raises(ValueError):
        memory.forget(entries, pref, NOW + 11, "caller")
    # Withdrawing consent takes everything with it.
    entries = memory.forget(entries, entries[0]["id"], NOW + 20, "caller")
    assert memory.active(entries, NOW + 20) == []
    with pytest.raises(ValueError, match="no consent"):
        memory.add(entries, "task", "x", "y", NOW + 30)


def test_only_the_latest_summary_is_kept_and_numbers_are_masked():
    entries = memory.add(consented(), "summary", "Asked about a travel notice.", "Next.", NOW)
    entries = memory.add(
        entries, "summary", "Gave card 4520 1234 5678 9012 by mistake.", "Next.", NOW + 60
    )
    latest = memory.summary(entries, NOW + 60)
    assert latest["text"] == "Gave card [number removed] by mistake."
    assert entries[1]["by"] == "replaced" and entries[1]["text"] is None


def test_the_file_has_a_cap():
    entries = consented()
    for i in range(memory.MAX_ACTIVE):
        entries = memory.add(entries, "preference", f"Note {i}.", "Why.", NOW)
    with pytest.raises(ValueError, match="full"):
        memory.add(entries, "preference", "One too many.", "Why.", NOW)


def test_context_block_tells_the_model_only_what_is_on_file():
    assert "no consent" in memory.context_block([], NOW)
    entries = memory.add(consented(), "task", "Owed a call about the wire.", "Why.", NOW)
    entries = memory.add(entries, "summary", "Asked about a wire.", "Next.", NOW)
    block = memory.context_block(entries, NOW + 60)
    assert f"[{entries[1]['id']}] task: Owed a call about the wire." in block
    assert "Last call: Asked about a wire." in block
    assert memory.is_returning(entries, NOW + 60)
    assert not memory.is_returning(consented(), NOW)


def test_snapshot_shows_what_why_and_until_when():
    entries = memory.add(consented(), "context", "Flying to Lisbon.", "So the card works.", NOW)
    entries = memory.add(entries, "task", "Owed a callback.", "Why.", NOW)
    entries = memory.forget(entries, entries[2]["id"], NOW + 5, "caller")
    state = {
        "entries": entries,
        "fresh": {entries[1]["id"]},
        "refused": [{"text": "Card [number removed]", "reason": "x"}] * 5,
        "loaded": 1,
    }
    snap = memory.snapshot(state, NOW + 10)
    assert snap["consent"]["expires"] == NOW + 30 * memory.DAY
    assert snap["memories"] == [
        {
            "id": entries[1]["id"],
            "kind": "context",
            "text": "Flying to Lisbon.",
            "why": "So the card works.",
            "saved": NOW,
            "expires": NOW + 3 * memory.DAY,
            "fresh": True,
        }
    ]
    assert snap["forgotten"] == [
        {"id": entries[2]["id"], "kind": "task", "at": NOW + 5, "by": "caller"}
    ]
    assert len(snap["refused"]) == 3 and snap["summary"] is None


def test_when_reads_like_speech():
    assert memory.when(7 * memory.DAY) == "7 days"
    assert memory.when(5 * 3600) == "5 hours"
    assert memory.when(30) == "1 minute"
