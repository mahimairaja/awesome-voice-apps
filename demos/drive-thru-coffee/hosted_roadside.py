"""Maintainer-owned adapter for the standalone roadside dispatch demo.

The contributed agent scores the caller's audio with Tyto (ai-coustics) and
re-confirms any detail captured while the line was bad. Hosting keeps that
behavior and only changes the plumbing: one shared Tyto model per worker, one
analyzer per call, and no scoring at all when the worker has no licence key.
"""

import asyncio
import importlib.util
import logging
import os
import sys
import threading
import types
from pathlib import Path

logger = logging.getLogger(__name__)

_demo = Path(__file__).resolve().parents[1] / "roadside-dispatch"


def _load(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


# The demo imports its sibling `health` module by plain name. Load it under a
# private name and expose it as `health` only while agent.py is executed.
_health = _load("playground_roadside_health", _demo / "health.py")
# The hosted session turns on VAD only, so the demo's turn-detector import is
# never used here. Stub it when the plugin (and its model) is not installed.
_stubs = {"health": _health}
try:
    import livekit.plugins.turn_detector.multilingual  # noqa: F401
except ImportError:
    _stubs["livekit.plugins.turn_detector"] = types.ModuleType("livekit.plugins.turn_detector")
    _multilingual = types.ModuleType("livekit.plugins.turn_detector.multilingual")
    _multilingual.MultilingualModel = None
    _stubs["livekit.plugins.turn_detector.multilingual"] = _multilingual
_saved = {name: sys.modules.get(name) for name in _stubs}
sys.modules.update(_stubs)
try:
    _module = _load("playground_roadside_agent", _demo / "agent.py")
finally:
    for name, previous in _saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous

aic = _module.aic
AudioHealth = _health.AudioHealth
RoadsideAgent = _module.RoadsideAgent
CLOSING_LINE = "Thank you. Help is on the way. Stay safe and stand clear of traffic."

_model = None
_model_lock = threading.Lock()


def tyto_model():
    """Load Tyto once per worker; the image bakes the file in at build time."""
    global _model
    with _model_lock:
        if _model is None:
            baked = sorted(_module.MODEL_DIR.glob("*.aicmodel"))
            path = baked[-1] if baked else aic.Model.download(_module.TYTO_MODEL, "/tmp/tyto")
            _model = aic.Model.from_file(str(path))
        return _model


def initial_state() -> dict:
    # Captured fields and audio health live on the per-call agent, not userdata.
    return {}


class RoadsideDispatcher(RoadsideAgent):
    """The contributed dispatcher with per-call health state and scoring hooks."""

    def __init__(self, room) -> None:
        super().__init__(room, AudioHealth())
        self._score_task: asyncio.Task | None = None
        self._ended = False

    def publish_roadside(self) -> None:
        _module._publish_warming(self.room)
        _module._publish_details(self.room, self.fields)

    async def start_scoring(self) -> None:
        """Score the visitor's audio with Tyto, or run as a plain dispatcher."""
        licence = os.environ.get("AIC_SDK_LICENSE")
        if not licence:
            logger.warning("AIC_SDK_LICENSE missing; roadside audio scoring is off")
            self._scoring_off()
            return
        try:
            analyzer = aic.FileAnalyzer(await asyncio.to_thread(tyto_model), licence)
        except Exception:  # noqa: BLE001 - a scoring failure must not end the call
            logger.warning("Tyto unavailable; roadside audio scoring is off")
            self._scoring_off()
            return
        on_window = _module._make_on_window(self.session, self, self.health)

        async def guarded_window() -> None:
            if not self._ended:
                await on_window()

        self._score_task = asyncio.create_task(
            _module._score_loop(self.room, analyzer, self.health, guarded_window)
        )
        self._score_task.add_done_callback(self._scoring_done)

    def _scoring_done(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            logger.warning("Roadside scoring stopped; the dispatcher keeps running")
            self._scoring_off()

    def _scoring_off(self) -> None:
        _module.publish_ui_event(
            self.room,
            "Card",
            _module._ui_action(self.room, "verdict"),
            component_id="verdict",
            props={"title": "status", "body": "audio scoring is off", "accent": False},
        )

    def stop_scoring(self) -> None:
        self._ended = True
        if self._score_task and not self._score_task.done():
            self._score_task.cancel()

    def watch_dispatch(self, end) -> None:
        """Close the call once the dispatch confirmation finishes playing."""

        def changed(event) -> None:
            # livekit-agents 1.x returns to "listening" after speech, not "idle".
            if (
                self.dispatched
                and not self._ended
                and event.old_state == "speaking"
                and event.new_state in ("listening", "idle")
            ):
                self._ended = True
                asyncio.create_task(self._close(end))

        self.session.on("agent_state_changed", changed)

    async def _close(self, end) -> None:
        try:
            handle = self.session.say(CLOSING_LINE, allow_interruptions=False)
            await handle
        except Exception:  # noqa: BLE001 - the call still has to end
            logger.warning("Roadside closing line failed")
        await end()
