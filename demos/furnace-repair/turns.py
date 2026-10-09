"""Shadow end-of-turn comparison for the furnace line.

The session commits turns with LiveKit's audio turn detector. Alongside it, a
plain silence timer runs in shadow on the same audio: every time VAD reports
the caller went quiet long enough, that timer would have ended the turn and
sent a half sentence to the LLM. This module records both decisions per turn
so a client can draw them on one timeline.

It never changes what the session does; it only watches.
"""

import time
from collections.abc import Callable
from typing import Any

from livekit.agents import inference

MAX_MARKS = 12
MAX_TEXT = 160


class TurnTimeline:
    """Per-call record of where a silence timer would cut versus the detector.

    Feed it session events; it calls ``publish(props)`` with a JSON-safe
    snapshot whenever the picture changes. Times are milliseconds from the
    start of the caller's turn.
    """

    def __init__(
        self,
        publish: Callable[[dict], None],
        *,
        timer_silence: float = 0.55,
        model: str = "turn-detector-v1",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._publish = publish
        # A VAD endpointer ends the turn once silence reaches this, so its
        # cut lands where VAD reports end of speech.
        self.timer_silence = timer_silence
        self.model = model
        self._clock = clock
        self.turns = 0
        self.saved = 0
        self.waited_ms = 0
        self._turn: dict | None = None
        self._heard_final = ""
        self._heard_interim = ""
        self._prediction: dict | None = None
        self._last: dict | None = None

    # Inputs -----------------------------------------------------------------

    def user_state(self, state: str, at: float | None = None) -> None:
        at = self._clock() if at is None else at
        if state == "speaking":
            turn = self._turn
            if turn is None:
                turn = self._turn = {
                    "start": at,
                    "segments": [],
                    "pauses": [],
                    "commit": None,
                    "said": "",
                }
                self._heard_final = self._heard_interim = ""
            elif turn["pauses"] and turn["pauses"][-1]["resumed"] is None:
                # The caller kept going after a pause the timer would have cut.
                pause = turn["pauses"][-1]
                pause["resumed"] = at
                self.saved += 1
            self._prediction = None
            if len(turn["segments"]) < MAX_MARKS:
                turn["segments"].append([at, None])
            self._emit()
        elif state == "listening" and self._turn is not None:
            turn = self._turn
            if turn["segments"] and turn["segments"][-1][1] is None:
                turn["segments"][-1][1] = at - self.timer_silence
            if len(turn["pauses"]) < MAX_MARKS:
                turn["pauses"].append(
                    {
                        "cut": at,
                        "quiet": at - self.timer_silence,
                        "p": None,
                        "th": None,
                        "resumed": None,
                        "heard": self.heard(),
                    }
                )
                if self._prediction:
                    turn["pauses"][-1].update(self._prediction)
            self._emit()

    def transcript(self, text: str, final: bool) -> None:
        text = " ".join(text.split())
        if final:
            self._heard_final = f"{self._heard_final} {text}".strip()
            self._heard_interim = ""
        else:
            self._heard_interim = text

    def prediction(self, probability: float, threshold: float | None) -> None:
        """A real detector verdict for the silence the caller is in now."""
        value = {
            "p": round(float(probability), 3),
            "th": None if threshold is None else round(float(threshold), 3),
        }
        self._prediction = value
        turn = self._turn
        if turn and turn["pauses"] and turn["pauses"][-1]["resumed"] is None:
            turn["pauses"][-1].update(value)
            self._emit()

    def model_changed(self, model: str) -> None:
        if model != self.model:
            self.model = model
            self._emit()

    def commit(self, text: str, at: float | None = None) -> None:
        """The detector (or its fallback) ended the caller's turn."""
        at = self._clock() if at is None else at
        turn = self._turn
        if turn is None:
            return
        turn["commit"] = at
        turn["said"] = " ".join(text.split())[:MAX_TEXT]
        if turn["segments"] and turn["segments"][-1][1] is None:
            # Committed on a transcript before VAD reported the silence.
            turn["segments"][-1][1] = at
        if turn["pauses"] and turn["pauses"][-1]["resumed"] is None:
            self.waited_ms += max(0, round((at - turn["pauses"][-1]["cut"]) * 1000))
        self.turns += 1
        self._emit()
        self._turn = None
        self._prediction = None

    # Output -----------------------------------------------------------------

    def heard(self) -> str:
        text = f"{self._heard_final} {self._heard_interim}".strip()
        return text[-MAX_TEXT:]

    def snapshot(self) -> dict:
        turn = self._turn or self._last
        totals = {"turns": self.turns, "saved": self.saved, "waited_ms": self.waited_ms}
        if turn is None:
            return {"turn": None, "model": self.model, "totals": totals}
        start = turn["start"]

        def ms(value: float | None) -> int | None:
            return None if value is None else max(0, round((value - start) * 1000))

        return {
            "turn": {
                "n": self.turns + (0 if turn["commit"] else 1),
                "segments": [[ms(a), ms(b)] for a, b in turn["segments"]],
                "pauses": [
                    {
                        "cut": ms(p["cut"]),
                        "quiet": ms(p["quiet"]),
                        "p": p["p"],
                        "th": p["th"],
                        "held": p["resumed"] is not None,
                        "heard": p["heard"],
                    }
                    for p in turn["pauses"]
                ],
                "commit": ms(turn["commit"]),
                "said": turn["said"],
            },
            "model": self.model,
            "totals": totals,
        }

    def _emit(self) -> None:
        if self._turn is not None:
            self._last = self._turn
        self._publish(self.snapshot())


class WatchedTurnDetector(inference.TurnDetector):
    """LiveKit's audio turn detector, reporting each verdict as it lands.

    Only real model answers are reported. A cancelled request (the caller
    spoke again) or a fallback default carries no inference timing, so it is
    skipped rather than drawn as a guess.
    """

    def __init__(
        self,
        *,
        on_prediction: Callable[[float, float | None], None],
        on_model: Callable[[str], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._on_prediction = on_prediction
        self._on_model = on_model

    def stream(self, **kwargs: Any):
        stream = super().stream(**kwargs)
        predict = stream.predict

        def watched():
            if stream.is_degraded:
                # Cloud detector gone with no local model: turns commit on the timer.
                self._on_model("off")
            future = predict()

            def report(done) -> None:
                if done.cancelled():
                    return
                event = done.result()
                if event.inference_duration is None and event.detection_delay is None:
                    return
                threshold = stream._opts.thresholds.lookup(None)
                self._on_model(stream.model)
                self._on_prediction(event.end_of_turn_probability, threshold)

            future.add_done_callback(report)
            return future

        stream.predict = watched
        return stream
