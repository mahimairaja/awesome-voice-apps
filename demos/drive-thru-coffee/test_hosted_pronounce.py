"""Offline tests for the hosted pronunciation coach; never call provider APIs."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import hosted
import hosted_pronounce
from livekit.agents import stt
from livekit.agents.types import TimedString

ROOM = "playground-598cd768-86d4-42a1-bb44-adc44fba4207"
OFFLINE = {
    "DEEPGRAM_API_KEY": "offline",
    "OPENAI_API_KEY": "offline",
    "CARTESIA_API_KEY": "offline",
}


def final(text: str, scores: dict[str, float]) -> stt.SpeechData:
    words = [TimedString(w, confidence=scores.get(w, 0.97)) for w in text.split()]
    return stt.SpeechData(language="en", text=text, words=words)


class HostedPronounce(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        hosted.claims.clear()

    def build(self):
        hosted.claims[ROOM] = {"id": "call", "seconds": 120}
        with (
            patch.dict(hosted.os.environ, OFFLINE),
            patch.object(
                hosted,
                "get_job_context",
                return_value=SimpleNamespace(room=SimpleNamespace(name=ROOM)),
            ),
        ):
            return hosted.HostedPronounce()

    async def test_registered_with_plain_nova3_and_sonic3_per_call(self):
        self.assertIs(hosted.CASCADE_AGENTS["pronounce"], hosted.HostedPronounce)
        first, second = self.build(), self.build()
        self.assertEqual((first.stt.model, first.stt._opts.keyterm), ("nova-3", []))
        self.assertEqual(first.tts._opts.model, "sonic-3")
        self.assertIsNot(first.stt, second.stt)
        self.assertIsNot(first.tts, second.tts)
        self.assertIn("two-minute limit", first.instructions)
        self.assertFalse(first._turn_handling["preemptive_generation"]["enabled"])
        self.assertEqual(first._turn_handling["endpointing"]["min_delay"], 0.9)
        self.assertNotIn("coach", sys.modules)
        await first.llm.aclose()
        await second.llm.aclose()

    async def test_choosing_french_switches_recogniser_and_voice(self):
        agent = self.build()
        state = agent.initial_state()
        with (
            patch.object(agent.stt, "update_options") as stt_options,
            patch.object(agent.tts, "update_options") as tts_options,
            patch.object(hosted_pronounce._module, "publish_ui_event") as publish,
        ):
            reply = await agent.choose_language(SimpleNamespace(userdata=state), "fr")
        stt_options.assert_called_once_with(language="fr")
        tts_options.assert_called_once_with(language="fr")
        self.assertIn(state["phrase"]["text"], reply)
        self.assertEqual(publish.call_args.args[1], "Pronounce")
        await agent.llm.aclose()

    async def test_words_from_every_final_segment_are_scored_at_turn_end(self):
        agent = self.build()
        state = agent.initial_state()
        hosted_pronounce.coach.choose_language(state, "en")
        phrase = next(p for p in hosted_pronounce.coach.PHRASES["en"][0] if "Thursday" in p["text"])
        state["phrase"] = {**phrase, "words": hosted_pronounce.coach.tokenize(phrase["text"])}
        agent.hear(final("I sink", {}))
        agent.hear(final("it is Thursday.", {"Thursday.": 0.5}))
        turn = MagicMock()
        with (
            patch.object(type(agent), "session", SimpleNamespace(userdata=state)),
            patch.object(hosted_pronounce._module, "publish_ui_event") as publish,
        ):
            await agent.on_user_turn_completed(turn, None)
        line = turn.add_message.call_args.kwargs["content"]
        self.assertIn("'think' (sounded like 'sink')", line)
        self.assertIn("'Thursday' (score 50)", line)
        self.assertEqual(state["mode"], "drill")
        props = publish.call_args.args[2]
        self.assertEqual(props["targets"], [1, 4])
        self.assertEqual(agent._heard, [])
        # Talk between reads is not scored and adds no system line.
        agent.hear(final("could you say that again please", {}))
        turn = MagicMock()
        with patch.object(type(agent), "session", SimpleNamespace(userdata=state)):
            await agent.on_user_turn_completed(turn, None)
        turn.add_message.assert_not_called()
        await agent.llm.aclose()


if __name__ == "__main__":
    unittest.main()
