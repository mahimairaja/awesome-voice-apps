"""Offline tests for the model switcher; never call provider APIs."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import hosted
import model_menu
from voicegateway.inference.pricing.catalog import calculate_cost_detail
from voicegateway.inference.session.capture import component_identity

ID = "598cd768-86d4-42a1-bb44-adc44fba4207"
ROOM = f"playground-{ID}"
KEYS = {
    "DEEPGRAM_API_KEY": "offline",
    "OPENAI_API_KEY": "offline",
    "CARTESIA_API_KEY": "offline",
    "SPEECHMATICS_API_KEY": "offline",
}
UNITS = {
    "stt": {"audio_seconds": 60},
    "llm": {"input_tokens": 1000, "output_tokens": 100},
    "tts": {"character_count": 1000},
}


def build(agent, metadata, keys=KEYS):
    room = SimpleNamespace(name=ROOM)
    hosted.claims[ROOM] = {"id": ID}
    job = SimpleNamespace(metadata=metadata if isinstance(metadata, str) else json.dumps(metadata))
    with (
        patch.dict(hosted.os.environ, keys, clear=True),
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
        self.assertEqual(model_menu.defaults(), model_menu.DEFAULT)

    def test_committed_menus_are_current(self):
        # Run `python playground/model_menu.py` after editing a models.toml or the catalog.
        self.assertEqual(model_menu.collect(), json.loads(model_menu.MENUS.read_text()))

    def test_a_demo_orders_and_locks_its_layers(self):
        menu = model_menu.menu_for("postop-checkin")
        self.assertEqual(menu["stt"][:2], ["deepgram-nova-3-medical", "deepgram-nova-3"])
        self.assertEqual(menu["llm"], model_menu.default_menu()["llm"])
        self.assertEqual(model_menu.defaults("postop-checkin")["stt"], "deepgram-nova-3-medical")
        # Its picks lead, and every other general model still follows.
        self.assertEqual(sorted(menu["stt"][2:]), sorted(model_menu.default_menu()["stt"][1:]))
        self.assertEqual(
            model_menu.parse_choice({"stt": "openai-gpt-4o-mini-transcribe"}, "postop-checkin"),
            {"stt": "openai-gpt-4o-mini-transcribe"},
        )
        self.assertEqual(
            model_menu.menus()["picks"]["postop-checkin"]["stt"],
            ["deepgram-nova-3-medical", "deepgram-nova-3"],
        )
        # One id locks the layer.
        self.assertEqual(model_menu.menu_for("panel-scribe")["llm"], ["openai-gpt-4o-mini"])
        self.assertEqual(model_menu.parse_choice({"llm": "openai-gpt-5"}, "panel-scribe"), {})
        # A specialist model shows only where a demo asks for it.
        self.assertNotIn("deepgram-nova-3-medical", model_menu.default_menu()["stt"])
        self.assertEqual(model_menu.parse_choice({"stt": "deepgram-nova-3-medical"}), {})

    def test_bad_models_toml_explains_itself(self):
        for text, message in (
            ('stt = ["whisper-large"]', "not in the catalog"),
            ('vision = ["openai-gpt-4.1-mini"]', "unknown layer"),
            ("stt = []", "non-empty list"),
            ('llm = ["openai-gpt-4o-mini", "openai-gpt-4o-mini"]', "twice"),
        ):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "models.toml"
                path.write_text(text)
                with self.assertRaisesRegex(ValueError, message):
                    model_menu.read_priority(path)

    def test_the_template_parses_to_the_default(self):
        template = model_menu.ROOT / "templates" / "livekit-base" / "models.toml"
        self.assertEqual(model_menu.read_priority(template), model_menu.default_menu())

    def test_every_model_is_priced_and_billed_by_an_accepted_provider(self):
        # An unpriced row stops the call; an unknown provider fails the sink.
        with patch.dict(hosted.os.environ, KEYS):
            for layer, menu in model_menu.MODELS.items():
                for model_id in menu:
                    with self.subTest(model=model_id):
                        provider, priced_as = component_identity(
                            model_menu.build({layer: model_id})[layer]
                        )
                        self.assertIn(provider, hosted.BILLED_PROVIDERS)
                        if provider == "speechmatics":
                            # Priced by the sink, not the catalog.
                            row = SimpleNamespace(
                                provider=provider, modality=layer, input_units=1, metadata={}
                            )
                            hosted.price_speechmatics(row)
                            self.assertGreater(row.cost_usd, 0)
                            continue
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

    async def test_speechmatics_leads_the_sponsored_demos(self):
        for agent_class in (
            hosted.HostedClaim,
            hosted.HostedClinic,
            hosted.HostedMortgage,
            hosted.HostedPanel,
        ):
            with self.subTest(demo=agent_class.demo):
                agent = build(agent_class, {"agent": agent_class.demo})
                self.assertEqual(identity(agent)["stt"], "speechmatics/linden-1")
                await agent.llm.aclose()
                # Deepgram stays one click away.
                agent = build(
                    agent_class, {"agent": agent_class.demo, "models": {"stt": "deepgram-nova-3"}}
                )
                self.assertEqual(identity(agent)["stt"], "deepgram/nova-3")
                await agent.llm.aclose()

    async def test_a_missing_sponsor_key_falls_back(self):
        # Before SPEECHMATICS_API_KEY is set in Railway, the demo runs Deepgram.
        keys = {k: v for k, v in KEYS.items() if k != "SPEECHMATICS_API_KEY"}
        for metadata in ({"agent": "claim"}, {"models": {"stt": "speechmatics-linden-1"}}):
            with self.subTest(metadata=metadata):
                agent = build(hosted.HostedClaim, metadata, keys)
                self.assertEqual(identity(agent)["stt"], "deepgram/nova-3")
                await agent.llm.aclose()

    async def test_other_demos_ignore_a_pick(self):
        agent = build(hosted.HostedTrivia, {"agent": "trivia", "models": {"tts": "openai-tts-1"}})
        self.assertEqual(identity(agent)["tts"], "cartesia/sonic-3")
        await agent.llm.aclose()


if __name__ == "__main__":
    unittest.main()
