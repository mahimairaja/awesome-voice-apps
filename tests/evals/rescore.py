"""Score saved calls again, offline, from their audio and turn metadata.

Run with: uv run python rescore.py <recordings_dir> [--calls d1.json]

``test_demos.py`` (with EVAL_AUDIO_DIR set) writes ``turns.json`` and one WAV
per turn into each simulation folder. DeepEval's own per-turn files are not
enough: it names them by caller turn, so when the agent sent a reply as two
segments the second overwrote the first, and it keeps no start times.

Calls saved before turns.json existed can be rebuilt with ``--calls``, the
stored call rows (a D1 export: ``[{"results": [...]}]``), matched to the
simulation folders in time order. Start times come from the rows' ``start_s``.
The last segment of each reply is DeepEval's own file. An earlier segment was
overwritten, so it is cut from ``deepeval-conversation.wav`` from its start,
after taking out every clip that is known exactly; its trailing silence is
approximate.

Agent Intelligibility needs faster-whisper. It runs only when the model is
already cached; nothing is downloaded.
"""

from __future__ import annotations

import io
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np
from deepeval.test_case import Audio, ConversationalTestCase, Turn
from deepeval.voice.connectors import audio_utils

TURNS_FILE = "turns.json"
# The rows round start_s to 1 ms; a clip is searched this far either side.
ALIGN_SAMPLES = 48
REPORTED = ["voice_consistency", "agent_intelligibility", "turn_taking_naturalness"]


# Saving --------------------------------------------------------------------


def save_turns(case: ConversationalTestCase, golden, folder: Path) -> None:
    """Write each turn's clip and a turns.json that points at it."""
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for index, turn in enumerate(case.turns):
        item = {"role": turn.role, "text": turn.content or ""}
        audio = turn.audio
        if audio is not None:
            name = f"turn-{index:02d}-{turn.role}.wav"
            (folder / name).write_bytes(audio.get_bytes())
            item["audio"] = name
            item["start_s"] = audio.start_time
        if turn.latency_ms is not None:
            item["latency_ms"] = turn.latency_ms
        out.append(item)
    meta = golden.additional_metadata or {}
    body = {"golden": golden.name, "demo": meta.get("demo"), "turns": out}
    (folder / TURNS_FILE).write_text(json.dumps(body, indent=1))


# Loading -------------------------------------------------------------------


def clip(data: bytes, start_s: float | None) -> Audio:
    return Audio.from_bytes(data, "audio/wav", start_time=start_s)


def turn(item: dict, audio: Audio | None) -> Turn:
    return Turn(
        role=item["role"],
        content=item.get("text") or "",
        audio=audio,
        latency_ms=item.get("latency_ms"),
    )


def load_case(folder: Path) -> tuple[str, ConversationalTestCase]:
    """A test case from a folder the harness wrote turns.json into."""
    body = json.loads((folder / TURNS_FILE).read_text())
    turns = []
    for item in body["turns"]:
        name = item.get("audio")
        audio = clip((folder / name).read_bytes(), item.get("start_s")) if name else None
        turns.append(turn(item, audio))
    return body["golden"], ConversationalTestCase(turns=turns, name=body["golden"])


def pcm(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2"), wav.getframerate()


def wav(samples: np.ndarray, rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(np.clip(samples, -32768, 32767).astype("<i2").tobytes())
    return out.getvalue()


def legacy_case(folder: Path, row: dict) -> tuple[ConversationalTestCase, float]:
    """Rebuild a pre-turns.json call from DeepEval's files and its stored row.

    Returns the case and the level (dBFS) of what is left of the call mix once
    every rebuilt clip is taken out: near silence means the rebuild is faithful.
    """
    items = json.loads(row["transcript"])
    # DeepEval's file name for each turn: turn-<caller turns so far>-<role>.
    names, number = [], 0
    for item in items:
        number += item["role"] == "user"
        names.append(f"deepeval-turn-{number}-{item['role']}.wav")
    last = {name: i for i, name in enumerate(names)}  # later segments overwrote earlier

    mix, rate = pcm(folder / "deepeval-conversation.wav")
    rest = mix.astype(np.int64)
    origin = min(item["start_s"] for item in items if "start_s" in item)
    at = {}
    for i, item in enumerate(items):
        if last[names[i]] != i or "start_s" not in item:
            continue
        samples, own_rate = pcm(folder / names[i])
        if own_rate != rate:  # the same resampler the mix was rendered with
            samples = np.frombuffer(
                audio_utils.resample_pcm16(samples.tobytes(), own_rate, rate), dtype="<i2"
            )
        guess = int(round((item["start_s"] - origin) * rate))
        best = None
        for offset in range(max(0, guess - ALIGN_SAMPLES), guess + ALIGN_SAMPLES + 1):
            window = rest[offset : offset + len(samples)]
            if len(window) < len(samples):
                break
            error = float(np.sum((window - samples) ** 2))
            if best is None or error < best[0]:
                best = (error, offset)
        if best is not None:
            rest[best[1] : best[1] + len(samples)] -= samples
            at[i] = best[1]

    turns, cut = [], np.zeros(len(rest), dtype=bool)
    for i, item in enumerate(items):
        if "start_s" not in item and last[names[i]] != i:
            turns.append(turn(item, None))  # overwritten and untimed: lost
            continue
        if last[names[i]] == i:
            path = folder / names[i]
            audio = clip(path.read_bytes(), item.get("start_s")) if path.exists() else None
            turns.append(turn(item, audio))
            continue
        # An overwritten segment: what remains of the mix from its start to the
        # next overwritten one's. Its clip can run past the next segment's
        # start (DeepEval dates a clip by its first voiced frame but keeps the
        # silence before it), and every clip known exactly is already out.
        start = int(round((item["start_s"] - origin) * rate))
        following = [
            it["start_s"]
            for j, it in enumerate(items[i + 1 :], i + 1)
            if last[names[j]] != j and "start_s" in it
        ]
        end = int(round((following[0] - origin) * rate)) if following else len(rest)
        piece = rest[start:end]
        voiced = np.flatnonzero(piece)
        piece = piece[: voiced[-1] + 1] if len(voiced) else piece[:0]
        cut[start : start + len(piece)] = True
        turns.append(turn(item, clip(wav(piece, rate), item["start_s"])))
    left = rest[~cut].astype(np.float64) / 32768.0
    level = 20 * np.log10(max(float(np.sqrt(np.mean(left**2))) if len(left) else 0.0, 1e-9))
    return ConversationalTestCase(turns=turns, name=row["golden"]), level


# Scoring -------------------------------------------------------------------


def cached_whisper():
    """WhisperSTT when its model is already on disk, else None. Never downloads."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from speech import WhisperSTT

    stt = WhisperSTT()
    try:
        stt._model()
    except Exception:  # noqa: BLE001 (not cached)
        return None
    return stt


def score(case: ConversationalTestCase, stt) -> dict:
    from metrics import AgentIntelligibilityMetric, voice_metrics
    from report import metric_key

    measured = voice_metrics() + ([AgentIntelligibilityMetric(stt)] if stt else [])
    out = {}
    for metric in measured:
        metric.measure(case, _show_indicator=False)
        breakdown = getattr(metric, "score_breakdown", None) or {}
        out[metric_key(metric)] = (metric.score, breakdown.get("no_audio_turns") or [])
    return out


def stored(row: dict) -> dict:
    return {s["metric"]: s.get("score") for s in json.loads(row.get("scores") or "[]")}


def fmt(value) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    root = Path(argv[0])
    rows = []
    if "--calls" in argv:
        export = json.loads(Path(argv[argv.index("--calls") + 1]).read_text())
        rows = sorted(
            (r for part in export for r in part["results"]), key=lambda r: r["created_at"]
        )
    stt = cached_whisper()
    if stt is None:
        print("faster-whisper is not cached: Agent Intelligibility is skipped.\n")

    folders = sorted(p for p in root.glob("simulation-*") if p.is_dir())
    header = " | ".join(f"{key} before / after" for key in REPORTED)
    print(f"| call | {header} | no audio | rebuild residual |")
    print("|---" * (len(REPORTED) + 3) + "|")
    legacy = iter(rows)
    for folder in folders:
        before, residual = {}, ""
        if (folder / TURNS_FILE).exists():
            name, case = load_case(folder)
        else:
            row = next(legacy, None)
            if row is None:
                print(f"| {folder.name} | no stored call, skipped |")
                continue
            files = len(list(folder.glob("deepeval-turn-*-user.wav")))
            users = sum(t["role"] == "user" for t in json.loads(row["transcript"]))
            if files != users:
                print(f"| {folder.name} | {users} caller turns but {files} files, skipped |")
                continue
            case, level = legacy_case(folder, row)
            name, before, residual = row["golden"], stored(row), f"{level:.0f} dBFS"
        after = score(case, stt)
        silent = sorted({i for _, skipped in after.values() for i in skipped})
        cells = " | ".join(
            f"{fmt(before.get(key))} / {fmt(after.get(key, (None, []))[0])}" for key in REPORTED
        )
        print(f"| {name} | {cells} | {silent or '-'} | {residual or '-'} |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
