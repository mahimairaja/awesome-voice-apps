"""Offline safety tests; never call provider APIs."""

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import hosted
import hosted_water
from trivia import HostedTriviaHost, initial_state

ID = "598cd768-86d4-42a1-bb44-adc44fba4207"
ROOM = f"playground-{ID}"


class HostedSafety(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        hosted.claims.clear()

    def request(self, metadata=None, room=ROOM):
        return SimpleNamespace(
            job=SimpleNamespace(
                metadata=json.dumps(metadata or {"agent": "coffee", "reservation": ID})
            ),
            room=SimpleNamespace(name=room),
            accept=AsyncMock(),
            reject=AsyncMock(),
        )

    async def test_rejects_room_substitution_without_claim(self):
        request = self.request(room="other-production-room")
        with patch.object(hosted, "control", new_callable=AsyncMock) as control:
            await hosted.authorize(request)
            control.assert_not_awaited()
        request.reject.assert_awaited_once()
        request.accept.assert_not_awaited()

    async def test_rejects_failed_server_claim(self):
        request = self.request()
        with patch.object(hosted, "control", new_callable=AsyncMock, side_effect=RuntimeError()):
            await hosted.authorize(request)
        request.reject.assert_awaited_once()
        self.assertFalse(hosted.claims)

    async def test_accepts_only_server_deadline(self):
        request = self.request()
        approval = {"id": ID, "room": ROOM, "deadline": time.time() + 60, "seconds": 60}
        with patch.object(hosted, "control", new_callable=AsyncMock, return_value=approval):
            await hosted.authorize(request)
        request.accept.assert_awaited_once()
        self.assertEqual(hosted.claims[ROOM], approval)

    async def test_rejects_expired_or_excessive_deadline(self):
        for offset in (-1, 125):
            request = self.request()
            with patch.object(
                hosted,
                "control",
                new_callable=AsyncMock,
                return_value={
                    "id": ID,
                    "room": ROOM,
                    "deadline": time.time() + offset,
                    "seconds": 120,
                },
            ):
                await hosted.authorize(request)
            request.reject.assert_awaited_once()

    async def test_rejects_invalid_allowance_or_identity(self):
        for seconds in (None, True, 0, -1, 121, 60.5, "60"):
            with self.subTest(seconds=seconds):
                request = self.request()
                approval = {"id": ID, "room": ROOM, "deadline": time.time() + 60}
                if seconds is not None:
                    approval["seconds"] = seconds
                with patch.object(hosted, "control", new_callable=AsyncMock, return_value=approval):
                    await hosted.authorize(request)
                request.reject.assert_awaited_once()
                request.accept.assert_not_awaited()
                self.assertFalse(hosted.claims)
        request = self.request()
        approval = {"id": "another-call", "room": ROOM, "deadline": time.time() + 60, "seconds": 60}
        with patch.object(hosted, "control", new_callable=AsyncMock, return_value=approval):
            await hosted.authorize(request)
        request.reject.assert_awaited_once()
        self.assertFalse(hosted.claims)

    async def test_deadline_stops_call_without_browser(self):
        agent = SimpleNamespace(approval={"deadline": time.time() + 0.01}, _finish=AsyncMock())
        await asyncio.wait_for(hosted.HostedCoffee._watch_deadline(agent), timeout=1)
        agent._finish.assert_awaited_once()

    async def test_provider_work_stops_before_exceeding_call_caps(self):
        agent = object.__new__(hosted.HostedCoffee)
        agent.approval = {"seconds": 60}
        agent._llm_requests = 6
        agent._tts_bytes = 0
        agent._finish = AsyncMock()
        context = SimpleNamespace(to_dict=dict)
        chunks = [chunk async for chunk in agent.llm_node(context, [], None)]
        self.assertEqual(chunks, [])
        await asyncio.sleep(0)
        agent._finish.assert_awaited_once()
        agent._finish.reset_mock()

        async def text():
            yield "a" * 1900
            yield "b" * 200

        async def fake_tts(_agent, bounded, _settings):
            async for chunk in bounded:
                yield chunk

        with patch.object(hosted.DriveThruAttendant, "tts_node", fake_tts):
            spoken = [chunk async for chunk in agent.tts_node(text(), None)]
        self.assertEqual(sum(map(len, spoken)), 1900)
        await asyncio.sleep(0)
        agent._finish.assert_awaited_once()

    async def test_concurrent_agents_do_not_share_provider_clients(self):
        room_a, room_b = SimpleNamespace(name=ROOM), SimpleNamespace(name="playground-second")
        hosted.claims.update({room_a.name: {"id": ID}, room_b.name: {"id": "second"}})
        with (
            patch.dict(
                hosted.os.environ,
                {
                    "DEEPGRAM_API_KEY": "offline",
                    "OPENAI_API_KEY": "offline",
                    "CARTESIA_API_KEY": "offline",
                },
            ),
            patch.object(
                hosted,
                "get_job_context",
                side_effect=[SimpleNamespace(room=room_a), SimpleNamespace(room=room_b)],
            ),
        ):
            first, second = hosted.HostedCoffee(), hosted.HostedCoffee()
        self.assertIsNot(first.stt, second.stt)
        self.assertIsNot(first.llm, second.llm)
        self.assertIsNot(first.tts, second.tts)
        await first.llm.aclose()
        await second.llm.aclose()

    async def test_metering_is_per_call_and_duplicate_safe(self):
        with patch.dict(
            hosted.os.environ,
            {"VOICEGW_COLLECTOR_URL": "http://localhost:8080", "VOICEGW_API_KEY": "offline"},
        ):
            sink = hosted.PlaygroundSink(ID)

        def record(identifier, provider, cost):
            return SimpleNamespace(
                id=identifier,
                provider=provider,
                cost_usd=cost,
                model_id="test",
                input_units=0,
                output_units=0,
                modality="llm",
                project="mahimai-playground",
            )

        with (
            patch.object(hosted.RemoteCollectorSink, "log_request", new_callable=AsyncMock),
            patch.object(hosted, "control", new_callable=AsyncMock) as control,
        ):
            await sink.log_request(record("one", "openai", 0.0000012))
            await sink.log_request(record("one", "openai", 0.0000012))
            control.assert_any_await("usage", ID, service="openai", microusd=2)
            await sink.log_request(record("two", "openai", 0.000002))
            control.assert_any_await("usage", ID, service="openai", microusd=4)
            invalid = record("other", "openai", 1)
            invalid.project = "another-app"
            with self.assertRaises(ValueError):
                await sink.log_request(invalid)
            with self.assertRaises(ValueError):
                await sink.log_request(record("bad", "cartesia", float("nan")))

    async def test_server_constructs_with_real_livekit(self):
        env = {
            key: "offline-test-only"
            for key in (
                "PLAYGROUND_WORKER_SECRET",
                "PLAYGROUND_ORIGIN",
                "VOICEGW_COLLECTOR_URL",
                "VOICEGW_API_KEY",
                "DEEPGRAM_API_KEY",
                "OPENAI_API_KEY",
                "CARTESIA_API_KEY",
            )
        }
        with patch.dict(hosted.os.environ, env):
            server = hosted.build_server()
        self.assertIsInstance(server, hosted.AgentServer)

    async def test_trivia_scoring_is_ordered_idempotent_and_isolated(self):
        agent = HostedTriviaHost(SimpleNamespace())
        state = initial_state()
        other = initial_state()
        context = SimpleNamespace(userdata=state)
        with patch("trivia.publish_trivia"):
            await agent.answer_question(context, 2, "six")
            self.assertEqual(state["index"], 0)
            await agent.answer_question(context, 1, "Mercury")
            await agent.answer_question(context, 1, "Mercury")
            self.assertEqual(state["correct"], 1)
            await agent.answer_question(context, 2, "skip")
            await agent.answer_question(context, 3, "H two O")
        self.assertEqual(state["correct"], 2)
        self.assertEqual(state["index"], 3)
        self.assertEqual(other["index"], 0)

    async def test_demo_substitution_is_rejected(self):
        request = self.request({"agent": "trivia", "reservation": ID})
        approval = {
            "id": ID,
            "room": ROOM,
            "deadline": time.time() + 60,
            "seconds": 60,
            "demo": "coffee",
        }
        with patch.object(hosted, "control", new_callable=AsyncMock, return_value=approval):
            await hosted.authorize(request)
        request.reject.assert_awaited_once()
        self.assertFalse(hosted.claims)

    async def test_registry_admits_each_demo_and_nothing_else(self):
        self.assertEqual(hosted.DEMOS, {"coffee", "trivia", "water", "sdr"})
        for demo in hosted.DEMOS:
            metadata = json.dumps({"agent": demo, "reservation": ID})
            self.assertEqual(hosted.parse_job(metadata, ROOM), ID)
        with self.assertRaises(ValueError):
            hosted.parse_job(json.dumps({"agent": "clinic", "reservation": ID}), ROOM)
        for demo, agent in hosted.CASCADE_AGENTS.items():
            self.assertEqual(agent.demo, demo)
            self.assertTrue(agent.greeting)

    async def test_water_calls_start_empty_and_stay_isolated(self):
        agent = object.__new__(hosted.HostedWater)
        first, second = agent.initial_state(), agent.initial_state()
        self.assertEqual(first, {"glasses": 0, "goal": hosted_water.DEFAULT_GOAL})
        self.assertIsNot(first, second)
        coach = hosted_water.WaterCoach(SimpleNamespace())
        context = SimpleNamespace(userdata=first)
        with patch.object(hosted_water._module, "publish_ui_event") as publish:
            await coach.log_water(context, 3)
            await coach.remove_water(context, 1)
            await coach.set_goal(context, 6)
        self.assertEqual(first["glasses"], 2)
        self.assertEqual(first["goal"], 6)
        self.assertEqual(second["glasses"], 0)
        props = publish.call_args.kwargs["props"]
        self.assertEqual((props["value"], props["of"]), (2, 6))

    async def test_failed_accept_does_not_leak_claim(self):
        request = self.request()
        request.accept.side_effect = RuntimeError()
        with (
            patch.object(
                hosted,
                "control",
                new_callable=AsyncMock,
                return_value={"id": ID, "room": ROOM, "deadline": time.time() + 60, "seconds": 60},
            ),
            self.assertRaises(RuntimeError),
        ):
            await hosted.authorize(request)
        self.assertFalse(hosted.claims)


if __name__ == "__main__":
    unittest.main()
