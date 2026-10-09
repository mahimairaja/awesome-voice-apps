"""Offline safety tests; never call provider APIs."""

import asyncio
import json
import os
import sys
import time
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import hosted
import hosted_claim
import hosted_clinic
import hosted_deescalate
import hosted_tenant
import hosted_water
from livekit.agents.llm import ChatContext
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
        self.assertEqual(
            hosted.DEMOS,
            {"coffee", "trivia", "water", "clinic", "claim", "tenant", "deescalate", "sdr"},
        )
        for demo in hosted.DEMOS:
            metadata = json.dumps({"agent": demo, "reservation": ID})
            self.assertEqual(hosted.parse_job(metadata, ROOM), ID)
        with self.assertRaises(ValueError):
            hosted.parse_job(json.dumps({"agent": "roadside", "reservation": ID}), ROOM)
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

    async def test_tenant_adapter_leaves_no_shared_modules(self):
        self.assertNotIn("rag", sys.modules)
        self.assertNotIn("livekit.plugins.nvidia", sys.modules)
        self.assertNotIn("livekit.plugins.turn_detector.multilingual", sys.modules)
        self.assertIs(hosted_tenant._module.embed_query, hosted_tenant.embed_query)

    async def test_tenant_index_retries_after_failure(self):
        hosted_tenant._index = None
        failing = AsyncMock(side_effect=RuntimeError())
        with patch.object(hosted_tenant, "embed", failing):
            fallback = await hosted_tenant.tenant_index(None)
        self.assertEqual(fallback["vectors"].shape[0], 0)
        self.assertIn("Security deposits", "\n".join(fallback["texts"]))
        self.assertIsNone(hosted_tenant._index)

    async def test_tenant_turns_cite_matches_and_bill_the_call(self):
        hosted_tenant._index = None
        texts, _ = hosted_tenant._chunks()
        vectors = [[1.0 if i == j else 0.0 for j in range(len(texts))] for i in range(len(texts))]
        deposits = next(i for i, text in enumerate(texts) if text.startswith("Security deposits"))
        builds = AsyncMock(return_value=(vectors, 2000))
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test"}),
            patch.object(hosted_tenant, "embed", builds),
        ):
            agent = object.__new__(hosted.HostedTenant)
            hosted_tenant.TenantGuide.__init__(agent, SimpleNamespace())
            await agent.load_index()
        self.assertEqual(agent._index["vectors"].shape, (len(texts), len(texts)))
        agent.sink = SimpleNamespace(records={}, report=AsyncMock())
        turn = MagicMock()
        cases = [(vectors[deposits], "deposit question"), ([0.0] * len(texts), "hello")]
        with patch.object(hosted_tenant._module, "publish_ui_event") as publish:
            for vector, text in cases:
                with patch.object(hosted_tenant, "embed", AsyncMock(return_value=([vector], 7))):
                    await agent.on_user_turn_completed(turn, SimpleNamespace(text_content=text))
            await asyncio.sleep(0)
        mounted, unmounted = publish.call_args_list
        self.assertEqual(mounted.kwargs["props"]["title"], "Security deposits")
        self.assertEqual(unmounted.args[2], "unmount")
        self.assertIsNone(hosted_tenant.current_guide.get())
        self.assertEqual(
            sorted(agent.sink.records.values()),
            [("openai", 7 * hosted_tenant.EMBED_USD_PER_TOKEN)] * 2,
        )
        self.assertEqual(agent.sink.report.await_count, 2)
        hosted_tenant._index = None

    async def test_clinic_calls_start_with_fresh_slots_and_stay_isolated(self):
        agent = object.__new__(hosted.HostedClinic)
        first, second = agent.initial_state(), agent.initial_state()
        self.assertEqual(len(first["available_slots"]), 6)
        self.assertIsNone(first["booking"])
        self.assertIsNot(first["available_slots"], second["available_slots"])
        self.assertIsNot(first["ui_mounted"], second["ui_mounted"])
        scheduler = hosted_clinic.ClinicScheduler(SimpleNamespace())
        context = SimpleNamespace(userdata=first)
        with patch.object(hosted_clinic._module, "publish_ui_event") as publish:
            hosted_clinic.publish_clinic(scheduler.room, first)
            await scheduler.book_appointment(context, "s1", "Sam Rivera", "checkup")
            await scheduler.reschedule(context, "s3")
        self.assertEqual(first["booking"]["slot_id"], "s3")
        self.assertEqual(
            [s["id"] for s in first["available_slots"]], ["s1", "s2", "s4", "s5", "s6"]
        )
        self.assertEqual(len(second["available_slots"]), 6)
        self.assertIsNone(second["booking"])
        components = [(c.args[1], c.args[2]) for c in publish.call_args_list]
        self.assertEqual(components[0], ("List", "mount"))
        self.assertEqual(components[-1], ("Card", "update"))
        self.assertEqual(publish.call_args.kwargs["props"]["footer"], "rescheduled")
        with patch.object(hosted_clinic._module, "publish_ui_event") as publish:
            await scheduler.cancel_appointment(context)
        self.assertIsNone(first["booking"])
        self.assertEqual(len(first["available_slots"]), 6)
        self.assertEqual(publish.call_args.args[1:3], ("Card", "unmount"))

    async def test_claim_calls_start_empty_and_stay_isolated(self):
        agent = object.__new__(hosted.HostedClaim)
        first, second = agent.initial_state(), agent.initial_state()
        self.assertEqual(first, {"claim": {}, "claim_ref": None})
        self.assertIsNot(first["claim"], second["claim"])
        intake = hosted_claim.ClaimIntake(SimpleNamespace())
        context = SimpleNamespace(userdata=first)
        with patch.object(hosted_claim._module, "publish_ui_event") as publish:
            self.assertIn("rejected", await intake.record_field(context, "policy_number", "12"))
            self.assertIn("missing", await intake.file_claim(context))
            answers = {
                "claimant_name": "Alex Chen",
                "policy_number": "ab-123456",
                "date_of_loss": hosted_claim.today().isoformat(),
                "location": "Highway 401 near exit 320",
                "vehicle": "2021 Honda Civic",
                "description": "Rear-ended at a red light",
                "injuries": "No.",
                "drivable": "yeah",
            }
            for field, value in answers.items():
                self.assertEqual(
                    await intake.record_field(context, field, value), f"recorded {field}"
                )
            filed = await intake.file_claim(context)
            again = await intake.file_claim(context)
        self.assertEqual(first["claim"]["policy_number"], "AB123456")
        self.assertEqual((first["claim"]["injuries"], first["claim"]["drivable"]), ("no", "yes"))
        self.assertRegex(first["claim_ref"], rf"^CLM-{hosted_claim.today():%Y%m%d}-[0-9A-F]{{4}}$")
        self.assertEqual(filed, again)
        self.assertEqual(second, {"claim": {}, "claim_ref": None})
        components = [call.args[1] for call in publish.call_args_list]
        self.assertIn("Card", components)
        progress = [c.kwargs["props"] for c in publish.call_args_list if c.args[1] == "Stat"]
        self.assertEqual((progress[-1]["value"], progress[-1]["of"]), (8, 8))

    async def test_claim_dates_follow_the_calendar_not_worker_start(self):
        validate = hosted_claim._module.VALIDATORS["date_of_loss"]
        later = hosted_claim.today() + timedelta(days=2)
        with patch.object(hosted_claim, "today", return_value=later):
            self.assertTrue(validate((later - timedelta(days=1)).isoformat())[0])
            self.assertIn(f"{later:%B}", hosted_claim.instructions())
        self.assertFalse(validate(later.isoformat())[0])

    async def test_claim_loads_without_its_original_provider_plugins(self):
        for name in hosted_claim._PROVIDERS:
            self.assertNotIn(f"livekit.plugins.{name}", sys.modules)
        self.assertEqual(hosted.HostedClaim.llm_budget, 24)
        hosted.claims[ROOM] = {"id": ID}
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
                return_value=SimpleNamespace(room=SimpleNamespace(name=ROOM)),
            ),
        ):
            agent = hosted.HostedClaim()
        self.assertIn("record each of them", agent.instructions)
        self.assertTrue(type(agent.llm).__module__.startswith("livekit.plugins.openai"))
        await agent.llm.aclose()
        self.assertEqual(hosted.HostedGuard.llm_budget, 12)

    def deescalate_agents(self):
        rooms = [SimpleNamespace(name=ROOM), SimpleNamespace(name="playground-second")]
        hosted.claims.update(
            {rooms[0].name: {"id": ID, "seconds": 120}, rooms[1].name: {"id": "second"}}
        )
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
                side_effect=[SimpleNamespace(room=room) for room in rooms],
            ),
        ):
            return hosted.HostedDeescalate(), hosted.HostedDeescalate()

    async def test_deescalate_calls_keep_their_own_voice_bill_and_meter(self):
        first, second = self.deescalate_agents()
        module = hosted_deescalate._module
        self.assertIsNot(first.tts, second.tts)
        self.assertEqual(first.tts.model, "sonic-3")
        self.assertIsNot(first.bill, second.bill)
        self.assertIsNot(first.meter, second.meter)
        first._voice(module.VOICE_BY_BAND["heated"])
        self.assertEqual((first.tts._opts.speed, first.tts._opts.emotion), (0.88, ["sympathetic"]))
        self.assertEqual(second.tts._opts.speed, 0.95)
        with patch.object(module, "publish_ui_event"):
            await first.apply_credit(SimpleNamespace(), "protection")
        self.assertEqual(second.bill["protection"].credited, 0)
        self.assertEqual(first.llm_budget, 24)
        await first.llm.aclose()
        await second.llm.aclose()

    async def test_deescalate_turn_picks_a_move_and_hands_off_in_code(self):
        first, second = self.deescalate_agents()
        module = hosted_deescalate._module
        first._barged_in = True
        first._spoke_for = 2.0
        turn = ChatContext.empty()
        message = SimpleNamespace(text_content="This is a scam and I'm cancelling, damn it")
        with patch.object(module, "publish_ui_event") as publish:
            await first.on_user_turn_completed(turn, message)
            note = turn.items[-1].text_content
            self.assertIn("Take ownership", note)
            self.assertEqual(first.tts._opts.speed, 0.88)
            self.assertFalse(first.handed_off)
            await first.on_user_turn_completed(
                ChatContext.empty(),
                SimpleNamespace(text_content="Get me a supervisor, this is ridiculous garbage"),
            )
        self.assertTrue(first.handed_off)
        components = [call.args[1] for call in publish.call_args_list]
        self.assertEqual(components, ["Mood", "Mood", "Handoff"])
        packet = publish.call_args.args[3]
        self.assertTrue(packet["reason"].startswith("Frustration reached"))
        self.assertEqual(len(packet["open"]), 3)
        with patch.object(hosted, "spawn") as spawn:
            first.end_call()
        spawn.assert_called_once()
        spawn.call_args.args[0].close()
        await first.llm.aclose()
        await second.llm.aclose()

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
