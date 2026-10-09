"""Offline tests for the bilingual 311 demo; never call provider APIs."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import hosted
import hosted_city311

ROOM = "playground-598cd768-86d4-42a1-bb44-adc44fba4207"
OFFLINE = {
    "DEEPGRAM_API_KEY": "offline",
    "OPENAI_API_KEY": "offline",
    "CARTESIA_API_KEY": "offline",
}


class LanguageRouting(unittest.TestCase):
    def test_first_words_set_the_language(self):
        router = hosted_city311.LanguageRouter()
        decision = router.hear("fr", "Bonjour", 0.92)
        self.assertEqual((decision["outcome"], decision["speaking"]), ("start", "fr"))

    def test_short_or_unsure_words_do_not_flip_the_call(self):
        router = hosted_city311.LanguageRouter()
        router.hear("en", "Hi there, I want to report a pothole", 0.95)
        self.assertEqual(router.hear("fr", "oui", 0.9)["reason"], "too short")
        self.assertEqual(router.hear("en", "on Main Street", 0.9)["outcome"], "same")
        held = router.hear("fr", "il y a un trou énorme ici", 0.4)
        self.assertEqual((held["outcome"], held["reason"]), ("held", "low confidence"))
        self.assertEqual(router.speaking, "en")
        self.assertEqual(
            router.hear("es", "hola necesito ayuda por favor", 0.99)["reason"], "not offered"
        )
        self.assertEqual(router.speaking, "en")

    def test_enough_words_or_a_repeat_switches(self):
        router = hosted_city311.LanguageRouter()
        router.hear("en-US", "Hi, my garbage was not picked up", 0.95)
        switched = router.hear("fr-CA", "En fait, je préfère parler français", 0.88)
        self.assertEqual((switched["outcome"], switched["speaking"]), ("switch", "fr"))
        self.assertEqual(router.hear("en", "okay", 0.9)["outcome"], "held")
        twice = router.hear("en", "thanks", 0.9)
        self.assertEqual((twice["outcome"], twice["reason"]), ("switch", "twice in a row"))

    def test_asking_always_wins_and_unknown_languages_are_refused(self):
        router = hosted_city311.LanguageRouter()
        router.hear("en", "Can you help me in French please", 0.97)
        self.assertEqual(router.ask("fr")["reason"], "caller asked")
        self.assertEqual(router.speaking, "fr")
        with self.assertRaises(ValueError):
            router.ask("de")


class HostedCity311(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        hosted.claims.clear()

    def build(self):
        hosted.claims[ROOM] = {"id": "call"}
        with (
            patch.dict(hosted.os.environ, OFFLINE),
            patch.object(
                hosted,
                "get_job_context",
                return_value=SimpleNamespace(room=SimpleNamespace(name=ROOM)),
            ),
        ):
            return hosted.HostedCity311()

    async def test_adapter_leaves_no_stub_modules(self):
        for name in hosted_city311._STUBS:
            module = sys.modules.get(name)
            self.assertTrue(
                module is None or getattr(module, "MultilingualModel", None) is not None
            )
        self.assertEqual(hosted.CASCADE_AGENTS["city311"], hosted.HostedCity311)

    async def test_uses_code_switching_stt_and_a_voice_per_call(self):
        first, second = self.build(), self.build()
        self.assertEqual(first.stt._opts.language, "multi")
        self.assertEqual(first.stt.model, "nova-3")
        self.assertEqual(first.stt._opts.endpointing_ms, 100)
        self.assertIsNot(first.tts, second.tts)
        self.assertIsNot(first.stt, second.stt)
        await first.llm.aclose()
        await second.llm.aclose()

    async def test_voice_and_prompt_follow_the_router(self):
        agent = self.build()
        state = agent.initial_state()
        agent._session = None
        with (
            patch.object(type(agent), "session", SimpleNamespace(userdata=state)),
            patch.object(hosted_city311._module, "publish_ui_event") as publish,
        ):
            agent.hear("fr", "Bonjour, il y a un nid-de-poule sur la rue Principale", 0.91)
            agent._sync_voice()
            turn = MagicMock()
            await agent.on_user_turn_completed(turn, None)
        self.assertEqual(agent.tts._opts.language, "fr")
        self.assertEqual(agent.tts._opts.voice, hosted_city311._module.LANGUAGES["fr"]["voice"])
        self.assertIn("Canadian French", turn.add_message.call_args.kwargs["content"])
        props = publish.call_args.kwargs.get("props") or publish.call_args.args[3]
        self.assertEqual((props["outcome"], props["voice"]), ("start", "French voice"))
        self.assertLessEqual(len(props["excerpt"]), 80)
        await agent.llm.aclose()

    async def test_tickets_and_schedules_are_per_call_and_localized(self):
        first, second = hosted_city311.initial_state(), hosted_city311.initial_state()
        self.assertIsNot(first["router"], second["router"])
        agent = hosted_city311.City311Agent.__new__(hosted_city311.City311Agent)
        agent.room = SimpleNamespace()
        context = SimpleNamespace(userdata=first)
        first["router"].hear("fr", "Bonjour, je veux signaler un problème", 0.9)
        with patch.object(hosted_city311._module, "publish_ui_event") as publish:
            self.assertIn("Ask for a street", await agent.report_issue(context, "pothole", " "))
            filed = await agent.report_issue(context, "pothole", "rue Principale et 3e Avenue")
            days = await agent.collection_schedule(context, "les-saules")
        ticket = first["tickets"][0]
        self.assertRegex(ticket["ref"], r"^311-\d{6}$")
        self.assertEqual((ticket["label"], ticket["language"]), ("Nid-de-poule", "fr"))
        self.assertIn("Français", filed)
        self.assertIn("mardi", days)
        self.assertEqual(second["tickets"], [])
        self.assertEqual(
            [c.args[1] for c in publish.call_args_list], ["ServiceRequest", "Collection"]
        )
        with patch.object(hosted_city311._module, "publish_ui_event"):
            for _ in range(2):
                await agent.report_issue(context, "graffiti", "Northgate library")
            capped = await agent.report_issue(context, "noise", "Main and 1st")
        self.assertIn("up to three", capped)


if __name__ == "__main__":
    unittest.main()
