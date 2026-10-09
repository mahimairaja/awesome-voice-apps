"""Offline tests: the phone line, the scoring and the tools. No provider calls."""

import random
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import agent
import numpy as np
import pytest
from livekit import rtc
from phone_line import PACKET, PhoneLine, mu_law
from scoring import digits, expected_tokens, matched, new_card, tokens

RATE = 24000


def tone_frames(seconds: float, hz: tuple[int, ...] = (440, 6000)) -> list[rtc.AudioFrame]:
    t = np.arange(int(RATE * seconds)) / RATE
    signal = sum(np.sin(2 * np.pi * f * t) for f in hz) * 8000
    samples = signal.astype(np.int16)
    step = RATE // 100
    return [
        rtc.AudioFrame(samples[i : i + step].tobytes(), RATE, 1, step)
        for i in range(0, len(samples), step)
    ]


def run(line: str, fixes: bool, seconds: float = 10, seed: int = 7):
    phone = PhoneLine(seed=seed)
    phone.set_line(line, fixes)
    out = []
    for frame in tone_frames(seconds):
        phone.note_rate(RATE)
        for played in phone.process(frame):
            assert played.sample_rate == RATE
            out.append(np.frombuffer(played.data, dtype=np.int16))
    return phone, np.concatenate(out)


def band(audio: np.ndarray, low: float, high: float) -> float:
    spectrum = np.abs(np.fft.rfft(audio[:RATE].astype(float)))
    freqs = np.fft.rfftfreq(RATE, 1 / RATE)
    return float(spectrum[(freqs > low) & (freqs < high)].max())


def test_web_line_passes_audio_through_untouched():
    frame = tone_frames(0.01)[0]
    phone = PhoneLine()
    phone.note_rate(RATE)
    assert phone.process(frame) == [frame]


def test_phone_lines_cut_everything_above_telephone_band():
    for line in ("landline", "cell"):
        _, audio = run(line, False, seconds=2)
        assert band(audio, 5900, 6100) < 0.01 * band(audio, 430, 450)


def test_mu_law_keeps_shape_with_eight_bit_steps():
    samples = (np.sin(np.linspace(0, 20, PACKET)) * 20000).astype(np.int16)
    out = mu_law(samples)
    assert np.corrcoef(samples, out)[0, 1] > 0.99
    assert len(np.unique(out)) <= 256


def test_bad_cell_drops_audio_and_fixes_recover_most_of_it():
    broken, _ = run("cell", False)
    fixed, _ = run("cell", True)
    assert broken.stats.packets == fixed.stats.packets > 400
    # Without fixes every lost or late packet becomes silence.
    assert broken.stats.silent == broken.stats.lost + broken.stats.late
    assert broken.stats.silent / broken.stats.packets > 0.08
    # With fixes the jitter buffer waits, FEC rebuilds and PLC fills the rest.
    assert fixed.stats.late < broken.stats.late
    assert fixed.stats.recovered > 0
    assert fixed.stats.silent < broken.stats.silent / 4


def test_toggling_fixes_drops_the_held_packet():
    phone, _ = run("cell", True, seconds=1)
    assert phone._held is not None
    phone.set_line("cell", False)
    assert phone._held is None
    phone.set_line("cell", True)
    assert phone._held is None


def test_landline_loses_nothing():
    phone, _ = run("landline", False, seconds=2)
    assert phone.stats.lost == phone.stats.late == phone.stats.silent == 0


def test_playback_clip_comes_from_the_tape():
    phone, _ = run("cell", False, seconds=3)
    clip = phone.clip(phone.samples_out - RATE, phone.samples_out)
    assert len(clip) == pytest.approx(RATE * 1.4, abs=RATE * 0.05)  # one second plus pre-roll
    assert len(phone.clip(phone.samples_out, phone.samples_out)) == 0


def test_tokens_spell_numbers_as_digits():
    assert tokens("Eighteen Thistlewood Cres, account 4471-2093") == [
        "1",
        "8",
        "thistlewood",
        "crescent",
        "account",
        *"44712093",
    ]
    assert digits("forty two, double four seven one, two oh nine three") == "424471" + "2093"


def test_matched_counts_in_order_tokens():
    card = {"house": "18", "street": "Thistlewood Crescent", "account": "4471 2093"}
    expected = expected_tokens(card)
    assert len(expected) == 12
    assert matched(expected, tokens("18 Thistlewood Crescent, 4471 2093")) == 12
    assert matched(expected, tokens("um it's 18 this will wood crescent 4471 2 3")) == 9
    assert matched(expected, tokens("switch me to a bad cell line")) == 0


def test_new_cards_differ_and_read_well():
    first, second = new_card(random.Random(1)), new_card(random.Random(2))
    assert first != second
    assert len(digits(first["account"])) == 8


@pytest.fixture
def outage():
    room = MagicMock()
    line = agent.OutageLine.__new__(agent.OutageLine)
    line.room = room
    line._stt = None
    line.line = PhoneLine(seed=3)
    line._confidences = []
    line._turn_start = None
    data = agent.initial_state(random.Random(5))
    session = SimpleNamespace(userdata=data, update_options=MagicMock(), stt=None)
    with (
        patch.object(agent.OutageLine, "session", session),
        patch.object(agent, "publish_ui_event"),
    ):
        line._open_reading()
        yield line, SimpleNamespace(userdata=data, session=session)


async def test_report_outage_checks_the_account_and_is_idempotent(outage):
    line, context = outage
    card = context.userdata["card"]
    wrong = await line.report_outage(context, "anywhere", "1234 5678")
    assert "No account matches" in wrong and context.userdata["ticket"] is None
    spoken = " ".join(digits(card["account"]))
    logged = await line.report_outage(context, card["street"], spoken)
    assert context.userdata["ticket"]["ref"] in logged
    assert "Already logged" in await line.report_outage(context, card["street"], spoken)


async def test_readings_are_scored_per_line(outage):
    line, context = outage
    card = context.userdata["card"]
    reading = f"{card['house']} {card['street']}, account {card['account']}"
    line._confidences = [0.9, 0.8]
    await line.on_user_turn_completed(None, SimpleNamespace(text_content=reading))
    first = context.userdata["readings"][-1]
    assert (first["line"], first["matched"], first["turns"]) == ("web", 12, 1)
    assert first["confidence"] == 0.85

    await line.set_line(context, "cell", False)
    context.session.update_options.assert_called_with(endpointing_opts={"min_delay": 0.5})
    # A request that carries no part of the card does not join a reading.
    await line.on_user_turn_completed(None, SimpleNamespace(text_content=reading[:6]))
    await line.on_user_turn_completed(None, SimpleNamespace(text_content="turn the fixes on"))
    cell = context.userdata["readings"][-1]
    assert (cell["line"], cell["turns"]) == ("cell", 1)
    assert cell["network"] is not None

    await line.set_line(context, "cell", True)
    context.session.update_options.assert_called_with(endpointing_opts={"min_delay": 0.9})
    shown = agent.panel(context.userdata, line.line)
    assert [r["line"] for r in shown["readings"]] == ["web", "cell", "cell"]
    assert all(not key.startswith("_") for r in shown["readings"] for key in r)


async def test_playback_needs_a_reading(outage):
    line, context = outage
    assert "no reading" in await line.play_back(context)
