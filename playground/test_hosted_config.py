"""Versioned demo configs: admission and per-call application. Offline only."""

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import demo_config
import hosted

ID = "598cd768-86d4-42a1-bb44-adc44fba4207"
ROOM = f"playground-{ID}"
KEYS = {
    "DEEPGRAM_API_KEY": "offline",
    "OPENAI_API_KEY": "offline",
    "CARTESIA_API_KEY": "offline",
    "LIVEKIT_API_KEY": "offline-key",
    "LIVEKIT_API_SECRET": "offline-secret-at-least-32-characters-long",
}
CONFIG = {
    "kind": "config",
    "toolset": "coffee",
    "instructions": "You take coffee orders for John Doe's cafe.",
    "greeting": "Greet the caller and ask for their order.",
    "models": {"llm": "openai/gpt-4.1-mini", "tts": "cartesia/sonic-3:voice-1"},
}


def request(metadata):
    """A fake OpenRTC job request carrying this dispatch metadata."""
    return SimpleNamespace(
        job=SimpleNamespace(metadata=json.dumps(metadata)),
        room=SimpleNamespace(name=ROOM),
        accept=AsyncMock(),
        reject=AsyncMock(),
    )


def approval():
    """A valid site claim answer for the test reservation."""
    return {"id": ID, "room": ROOM, "deadline": time.time() + 60, "seconds": 60}


class Admission(unittest.IsolatedAsyncioTestCase):
    """authorize() fetches and checks the signed config before accepting a call."""

    def setUp(self):
        hosted.claims.clear()

    async def test_no_version_fetches_nothing(self):
        req = request({"agent": "coffee", "reservation": ID})
        with patch.object(hosted, "control", new_callable=AsyncMock, return_value=approval()) as c:
            await hosted.authorize(req)
        req.accept.assert_awaited_once()
        self.assertEqual(c.await_count, 1)
        self.assertIsNone(hosted.claims[ROOM]["config"])

    async def test_fetches_the_signed_version_once(self):
        req = request({"agent": "coffee", "reservation": ID, "version": 3})
        answers = [approval(), {"config": CONFIG}]
        with patch.object(hosted, "control", new_callable=AsyncMock, side_effect=answers) as c:
            await hosted.authorize(req)
        req.accept.assert_awaited_once()
        c.assert_awaited_with("config", ID, demo="coffee", version=3)
        config = hosted.claims[ROOM]["config"]
        self.assertEqual(config.version, 3)
        self.assertEqual(config.models["tts"], "cartesia/sonic-3:voice-1")

    async def test_bad_version_or_config_rejects_before_inference(self):
        bad = [
            ({"version": 0}, CONFIG),
            ({"version": "3"}, CONFIG),
            ({"version": True}, CONFIG),
            ({"version": 3}, {**CONFIG, "toolset": "trivia"}),
            ({"version": 3}, {**CONFIG, "extra": 1}),
            ({"version": 3}, {**CONFIG, "models": {"realtime": "openai/gpt-realtime"}}),
            ({"version": 3}, {**CONFIG, "models": {"llm": "gpt 4"}}),
            ({"version": 3}, {**CONFIG, "models": {"stt": "deepgram/nova-3:en"}}),
            ({"version": 3}, {**CONFIG, "instructions": " "}),
            ({"version": 3}, None),
            ({"version": 3}, {**CONFIG, "models": []}),
            ({"version": 3}, {**CONFIG, "models": ""}),
        ]
        for extra, config in bad:
            with self.subTest(extra=extra, config=config):
                hosted.claims.clear()
                req = request({"agent": "coffee", "reservation": ID, **extra})
                answers = [approval(), {"config": config}]
                with patch.object(hosted, "control", new_callable=AsyncMock, side_effect=answers):
                    await hosted.authorize(req)
                req.reject.assert_awaited_once()
                req.accept.assert_not_awaited()
                self.assertFalse(hosted.claims)

    async def test_realtime_demo_takes_no_config(self):
        req = request({"agent": "sdr", "reservation": ID, "version": 1})
        answers = [{**approval(), "demo": "sdr"}, {"config": CONFIG}]
        with patch.object(hosted, "control", new_callable=AsyncMock, side_effect=answers):
            await hosted.authorize(req)
        req.reject.assert_awaited_once()


class PerCall(unittest.IsolatedAsyncioTestCase):
    """build_agent() applies the config after the demo has built itself."""

    def build(self, config, models=None, cls=None):
        """Build one call's agent through the OpenRTC factory, offline."""
        claim = {"id": ID, "seconds": 60, "config": config}
        hosted.claims[ROOM] = claim
        metadata = json.dumps({"agent": "coffee", "reservation": ID, "models": models or {}})
        ctx = SimpleNamespace(
            room=SimpleNamespace(name=ROOM), job=SimpleNamespace(metadata=metadata)
        )
        with (
            patch.dict(hosted.os.environ, KEYS),
            patch.object(hosted, "get_job_context", return_value=ctx),
        ):
            return hosted.build_agent(cls or hosted.HostedCoffee, ctx)

    async def test_config_sets_prompt_greeting_and_inference_models(self):
        config = demo_config.parse(CONFIG, "coffee", 3)
        agent = self.build(config)
        self.assertEqual(agent.instructions, CONFIG["instructions"])
        self.assertEqual(agent._config_greeting, CONFIG["greeting"])
        self.assertEqual(agent.llm.provider, "livekit")
        self.assertEqual(agent.tts.provider, "livekit")
        # STT is not in the config, so the menu default stays.
        self.assertNotEqual(agent.stt.provider, "livekit")
        # The class keeps its own greeting for calls without a config.
        self.assertNotEqual(hosted.HostedCoffee.greeting, CONFIG["greeting"])
        self.assertIn("livekit", hosted.BILLED_PROVIDERS)

    async def test_visitor_pick_beats_config_model(self):
        config = demo_config.parse(CONFIG, "coffee", 3)
        pick = next(iter(hosted.model_menu.menu_for("drive-thru-coffee")["llm"]))
        agent = self.build(config, {"llm": pick})
        self.assertNotEqual(agent.llm.provider, "livekit")
        self.assertEqual(agent.tts.provider, "livekit")

    async def test_config_wins_over_a_demo_that_sets_its_own_prompt(self):
        # Pharmacy assigns its prompt in its own __init__, after HostedGuard's.
        raw = {**CONFIG, "toolset": "pharmacy"}
        agent = self.build(demo_config.parse(raw, "pharmacy", 1), cls=hosted.HostedPharmacy)
        self.assertEqual(agent.instructions, CONFIG["instructions"])
        self.assertEqual(agent.llm.provider, "livekit")

    async def test_a_read_only_greeting_demo_still_builds(self):
        # Post-op computes its greeting as a property; the config keeps its own.
        raw = {**CONFIG, "toolset": "postop"}
        agent = self.build(demo_config.parse(raw, "postop", 1), cls=hosted.HostedPostop)
        self.assertEqual(agent._config_greeting, CONFIG["greeting"])

    async def test_no_config_keeps_the_demo(self):
        agent = self.build(None)
        self.assertIsNone(agent._config_greeting)
        self.assertNotEqual(agent.llm.provider, "livekit")


if __name__ == "__main__":
    unittest.main()
