"""Offline tests for the model switcher; never call provider APIs."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hosted
import model_menu
from voicegateway.inference.pricing.catalog import calculate_cost_detail
from voicegateway.inference.session.capture import component_identity

ID = "598cd768-86d4-42a1-bb44-adc44fba4207"
ROOM = f"playground-{ID}"
KEYS = {"DEEPGRAM_API_KEY": "offline", "OPENAI_API_KEY": "offline", "CARTESIA_API_KEY": "offline"}
UNITS = {
    "stt": {"audio_seconds": 60},
    "llm": {"input_tokens": 1000, "output_tokens": 100},
    "tts": {"character_count": 1000},
}


def build(agent, metadata):
    room = SimpleNamespace(name=ROOM)
    hosted.claims[ROOM] = {"id": ID}
    job = SimpleNamespace(metadata=metadata if isinstance(metadata, str) else json.dumps(metadata))
    with (
        patch.dict(hosted.os.environ, KEYS),
        patch.object(hosted, "get_job_context", return_value=SimpleNamespace(room=room, job=job)),
    ):
        return agent()


def identity(agent) -> dict[str, str]:
    return {layer: component_identity(getattr(agent, layer))[1] for layer in model_menu.LAYERS}


class ModelMenu(unittest.TestCase):
    def test_only_known_ids_survive(self):
        self.assertEqual(model_menu.parse_choice(None), {})
        self.assertEqual(model_menu.parse_choice(["deepgram-nova-3"]), {})
        self.assertEqual(
            model_menu.parse_choice(
                {
                    "stt": "openai-gpt-4o-mini-transcribe",
                    "llm": "anthropic-claude",  # not on the menu
                    "tts": "deepgram-nova-3",  # an STT id in the TTS slot
                    "vision": "openai-gpt-4.1-mini",
                }
            ),
            {"stt": "openai-gpt-4o-mini-transcribe"},
        )

    def test_default_is_on_the_menu(self):
        self.assertEqual(model_menu.parse_choice(model_menu.DEFAULT), model_menu.DEFAULT)

    def test_every_model_is_priced_and_billed_by_an_accepted_provider(self):
        # An unpriced row stops the call; an unknown provider fails the sink.
        with patch.dict(hosted.os.environ, KEYS):
            for layer, menu in model_menu.MODELS.items():
                for model_id in menu:
                    with self.subTest(model=model_id):
                        provider, priced_as = component_identity(
                            model_menu.build({layer: model_id})[layer]
                        )
                        self.assertIn(provider, {"openai", "deepgram", "cartesia"})
                        cost, unrated = calculate_cost_detail(layer, priced_as, **UNITS[layer])
                        self.assertGreater(cost, 0)
                        self.assertEqual(unrated, ())


class SwitchableDemo(unittest.IsolatedAsyncioTestCase):
    async def test_coffee_runs_the_visitors_pick(self):
        agent = build(
            hosted.HostedCoffee,
            {"agent": "coffee", "models": {"llm": "openai-gpt-4.1-nano", "tts": "deepgram-aura-2"}},
        )
        self.assertEqual(
            identity(agent),
            {
                "stt": "deepgram/nova-3",
                "llm": "openai/gpt-4.1-nano",
                "tts": "deepgram/aura-2-thalia-en",
            },
        )
        await agent.llm.aclose()

    async def test_bad_metadata_keeps_the_default(self):
        for metadata in ("not json", {"models": "openai"}, {"models": {"llm": "gpt-5"}}):
            with self.subTest(metadata=metadata):
                agent = build(hosted.HostedCoffee, metadata)
                self.assertEqual(identity(agent)["llm"], "openai/gpt-4o-mini")
                await agent.llm.aclose()

    async def test_other_demos_ignore_a_pick(self):
        agent = build(hosted.HostedTrivia, {"agent": "trivia", "models": {"tts": "openai-tts-1"}})
        self.assertEqual(identity(agent)["tts"], "cartesia/sonic-3")
        await agent.llm.aclose()


if __name__ == "__main__":
    unittest.main()
