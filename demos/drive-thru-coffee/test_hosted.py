"""Offline safety tests; never call provider APIs."""

import asyncio
import json
import os
import sys
import time
import unittest
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import hosted
import hosted_cancel
import hosted_claim
import hosted_clinic
import hosted_panel
import hosted_interp
import hosted_pharmacy
import hosted_furnace
import hosted_fraud
import hosted_interview
import hosted_mortgage
import hosted_router
import hosted_rebook
import hosted_returns
import hosted_outage
import hosted_postop
import hosted_deescalate
import hosted_recall
import hosted_tenant
import hosted_water
from livekit.agents.llm import ChatMessage
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
            {"coffee", "trivia", "water", "clinic", "claim", "tenant", "panel", "sdr"},
        )
        for demo in hosted.DEMOS:
            metadata = json.dumps({"agent": demo, "reservation": ID})
            self.assertEqual(hosted.parse_job(metadata, ROOM), ID)
        with self.assertRaises(ValueError):
            hosted.parse_job(json.dumps({"agent": "roadside", "reservation": ID}), ROOM)
        for demo, agent in hosted.CASCADE_AGENTS.items():
            self.assertEqual(agent.demo, demo)
            # The interview lobby hands off silently, and outbound calls listen
            # first; every other demo opens with a greeting.
            self.assertEqual(
                bool(agent.greeting), demo != "interview" and agent is not hosted.HostedDelivery
            )

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

    def site(self):
        """A stand-in for the site's recall action, using the demo's own policy."""
        memory = hosted_recall.memory
        files: dict[str, list] = {}

        async def control(action, reservation, op, **fields):
            self.assertEqual(action, "recall")
            entries, now = files.get(reservation, []), time.time()
            try:
                if op == "save":
                    entries = memory.add(
                        entries, fields["kind"], fields["text"], fields["why"], now
                    )
                elif op == "forget":
                    entries = memory.forget(entries, fields["memory"], now, "caller")
                elif op == "forget_all":
                    entries = memory.forget_all(entries, now, "caller")
            except ValueError as problem:
                return {"refused": str(problem)}
            files[reservation] = entries
            return {"entries": entries}

        return control, files

    async def test_recall_file_is_keyed_by_reservation_on_the_site(self):
        control, files = self.site()
        store = hosted_recall.SiteStore(control, ID)
        concierge = hosted_recall.Concierge(SimpleNamespace(), store)
        context = SimpleNamespace(userdata=hosted_recall.initial_state())
        with patch.object(hosted_recall._module, "publish_ui_event") as publish:
            self.assertIn(
                "no consent", await concierge.remember(context, "task", "Owed a call.", "x")
            )
            await concierge.record_consent(context, True)
            kept = await concierge.remember(context, "task", "Owed a call about the wire.", "x")
            self.assertTrue(kept.startswith("kept as m"))
            self.assertIn("deleted", await concierge.forget(context, files[ID][-1]["id"]))
        self.assertEqual([e["kind"] for e in files[ID]], ["consent", "task"])
        self.assertIsNone(files[ID][-1]["text"])
        self.assertEqual(publish.call_args.args[1], "Recall")
        # Another visitor's reservation sees an empty file.
        self.assertEqual(await hosted_recall.SiteStore(control, "other").load(), [])

    async def test_recall_adapter_leaves_no_shared_memory_module(self):
        self.assertNotIn("memory", sys.modules)
        self.assertIn("two-minute limit", hosted.HostedRecall.base_instructions)
        self.assertIn("record_consent", hosted.HostedRecall.base_instructions)

    async def test_recall_summarises_before_finish_and_bills_it(self):
        control, files = self.site()
        agent = object.__new__(hosted.HostedRecall)
        hosted_recall.Concierge.__init__(
            agent, SimpleNamespace(), hosted_recall.SiteStore(control, ID)
        )
        agent.approval = {"id": ID}
        agent._closing = False
        agent._deadline_task = None
        agent._active_session = MagicMock()
        agent.room = SimpleNamespace(disconnect=AsyncMock())
        agent.sink = SimpleNamespace(records={}, report=AsyncMock())
        state = hosted_recall.initial_state()
        agent._state = state
        context = SimpleNamespace(userdata=state)
        with patch.object(hosted_recall._module, "publish_ui_event"):
            await agent.record_consent(context, True)
        state["lines"] = ["Client: hi", "Concierge: hello", "Client: is my wire done?"]
        order = []
        finish = AsyncMock(side_effect=lambda *a, **k: order.append("finish") or {})

        async def summary(lines):
            order.append("summary")
            return "Asked whether the tuition wire went out.", 400, 30

        with (
            patch.object(hosted_recall._module, "summarize", summary),
            patch.object(hosted, "control", finish),
        ):
            await agent._finish()
            await agent._finish()
        self.assertEqual(order, ["summary", "finish"])
        self.assertEqual(files[ID][-1]["kind"], "summary")
        ((provider, cost),) = agent.sink.records.values()
        self.assertEqual(provider, "openai")
        self.assertEqual(cost, hosted_recall.summary_cost(400, 30))
        agent.sink.report.assert_awaited()
        agent._active_session.shutdown.assert_called_with(drain=False)

    async def test_recall_uses_sonic_3_and_a_bigger_budget(self):
        self.assertEqual(hosted.HostedRecall.llm_budget, 20)
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
            agent = hosted.HostedRecall()
        self.assertEqual(agent.tts._opts.model, "sonic-3")
        self.assertEqual(agent.store.reservation, ID)
        await agent.llm.aclose()

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

    async def test_panel_handoffs_attribute_lines_to_the_spoken_seat(self):
        self.assertEqual(hosted_panel.handoff("Engineer here."), ("Engineer", ""))
        self.assertEqual(
            hosted_panel.handoff("This is the recruiter, she was great with the team"),
            ("Recruiter", "she was great with the team"),
        )
        self.assertEqual(
            hosted_panel.handoff("Okay, switching to the hiring manager. Strong hire."),
            ("Hiring manager", "Strong hire."),
        )
        self.assertEqual(
            hosted_panel.handoff("I think the engineer liked her"),
            (None, "I think the engineer liked her"),
        )
        self.assertEqual(
            hosted_panel.handoff("Manager expects stronger tests"),
            (None, "Manager expects stronger tests"),
        )
        self.assertEqual(
            hosted_panel.handoff("I'm the engineer. Clean code."), ("Engineer", "Clean code.")
        )
        self.assertEqual(hosted_panel.handoff("I am the recruiter"), ("Recruiter", ""))
        room = SimpleNamespace()
        first = hosted_panel.SoloPanelScribe(room)
        second = hosted_panel.SoloPanelScribe(SimpleNamespace())

        def turn(text):
            return ChatMessage(role="user", content=[text])

        with patch.object(hosted_panel._module, "publish_ui_event") as publish:
            with self.assertRaises(hosted_panel.StopResponse):
                await first.on_user_turn_completed(None, turn("Engineer here."))

            async def say(text):
                msg = turn(text)
                with self.assertRaises(hosted_panel.StopResponse):
                    await first.on_user_turn_completed(None, msg)
                return msg

            msg = await say("Her system design was solid")
            self.assertEqual(msg.content, ["[Engineer] Her system design was solid"])
            await say("Recruiter. Great with people, wants remote")
            recap = turn("scribe recap")
            await first.on_user_turn_completed(None, recap)
        self.assertEqual(
            first.transcript,
            [
                {"speaker": "Engineer", "text": "Her system design was solid"},
                {"speaker": "Recruiter", "text": "Great with people, wants remote"},
                {"speaker": "Recruiter", "text": "scribe recap"},
            ],
        )
        self.assertEqual(second.transcript, [])
        self.assertEqual(second.sidecar.current_speaker, "Hiring manager")
        meters = [c.kwargs["props"] for c in publish.call_args_list if c.args[1] == "Meters"]
        self.assertEqual([item["label"] for item in meters[-1]["items"]], ["Engineer", "Recruiter"])
        with patch.object(hosted_panel._module, "publish_ui_event") as publish:
            row = hosted_panel.ScorecardRow(
                interviewer="Engineer", strengths="design", concerns="none", lean="hire"
            )
            unspoken = hosted_panel.ScorecardRow(
                interviewer="Hiring manager", strengths="-", concerns="-", lean="undecided"
            )
            await first.publish_scorecard(None, [row, unspoken], "Lean hire.")
        table = publish.call_args_list[0].kwargs["props"]
        self.assertEqual(table["rows"], [["Engineer", "design", "none", "hire"]])

    async def test_panel_loads_without_its_original_provider_plugins(self):
        for name in hosted_panel._PROVIDERS:
            self.assertNotIn(f"livekit.plugins.{name}", sys.modules)
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
            agent = hosted.HostedPanel()
        self.assertIn("one visitor plays the whole panel", agent.instructions)
        self.assertEqual(len(agent.tools), 1)
        await agent.llm.aclose()

    async def test_interpreter_pairs_each_line_with_what_was_said(self):
        for key in hosted_interp._STUBS:
            self.assertNotIn(key, sys.modules)
        finish = AsyncMock()
        spawn = MagicMock()
        args = (SimpleNamespace(name=ROOM), {"id": ID}, finish, spawn, None)
        with patch.dict(hosted.os.environ, {"OPENAI_API_KEY": "offline"}):
            agent = hosted.realtime_agent("interp", *args)
            other = hosted.realtime_agent("interp", *args)
            for demo in hosted.REALTIME_DEMOS:
                model = hosted.realtime_model(demo)
                self.assertTrue(type(model).__module__.startswith("livekit.plugins.openai"))
        self.assertIsInstance(agent, hosted_interp.HostedInterpreter)
        self.assertIn("one visitor plays both people", agent.instructions)
        self.assertIn("desk clerk who speaks English", agent.instructions)

        def said(role, text):
            return SimpleNamespace(role=role, text_content=text)

        with patch.object(hosted_interp._module, "publish_ui_event") as publish:
            agent.interpreted(said("assistant", "Hello, I am the interpreter."))
            agent.greeted = True
            agent.hear("Hola, tengo una reserva")
            agent.hear("a nombre de Ana.")
            agent.interpreted(said("user", "ignored"))
            agent.interpreted(said("assistant", "Hello, I have a booking under Ana."))
            agent.hear("Welcome, Ana.")
            agent.interpreted(said("assistant", "Bienvenida, Ana."))
        self.assertEqual(
            agent.captions,
            [
                {
                    "text": "Hello, I have a booking under Ana.",
                    "original": "Hola, tengo una reserva a nombre de Ana.",
                },
                {"text": "Bienvenida, Ana.", "original": "Welcome, Ana."},
            ],
        )
        self.assertEqual(other.captions, [])
        props = publish.call_args_list[-1].kwargs["props"]
        self.assertEqual(props["items"], agent.captions)
        created = {"type": "response.event", "event": {"type": "response.created"}}
        for _ in range(hosted_interp.MAX_RESPONSES):
            agent.budget_event(created)
        spawn.assert_not_called()
        agent.budget_event(created)
        spawn.assert_called_once()
        spawn.call_args.args[0].close()

    async def test_pharmacy_calls_get_their_own_prescription(self):
        self.assertNotIn("refill", sys.modules)
        agent = object.__new__(hosted.HostedPharmacy)
        first, second = agent.initial_state(), agent.initial_state()
        self.assertIsNot(first["fields"], second["fields"])
        line = hosted_pharmacy.RefillLine(SimpleNamespace())
        context = SimpleNamespace(userdata=first)
        rx = first["profile"]["rx_number"]
        wrong = rx[:-1] + ("D" if rx[-1] != "D" else "B")
        with patch.object(hosted_pharmacy._module, "publish_ui_event") as publish:
            self.assertIn("unconfirmed", await line.place_refill(context))
            await line.capture(context, "rx_number", wrong)
            mismatch = await line.confirm(context, "rx_number")
            self.assertTrue(mismatch.startswith("mismatch"))
            self.assertNotIn(rx, mismatch)
            answers = {
                "drug": first["profile"]["drug"],
                "rx_number": rx,
                "date_of_birth": first["profile"]["date_of_birth"],
                "postal_code": first["profile"]["postal_code"].lower(),
                "pickup_store": "king street",
            }
            for field, value in answers.items():
                self.assertNotIn("rejected", await line.capture(context, field, value))
                self.assertTrue((await line.confirm(context, field)).startswith("confirmed"))
            placed = await line.place_refill(context)
            again = await line.place_refill(context)
        self.assertEqual(placed, again)
        self.assertRegex(first["ref"], r"^RF-\d{4}-[0-9A-F]{4}$")
        self.assertIsNone(second["ref"])
        props = publish.call_args.args[2]
        self.assertEqual(publish.call_args.args[1], "Refill")
        self.assertEqual(props["ref"], first["ref"])
        self.assertTrue(all(f["status"] == "confirmed" for f in props["fields"]))

    async def test_pharmacy_logs_redacted_lines_and_spells_codes(self):
        line = hosted_pharmacy.RefillLine(SimpleNamespace())
        state = hosted_pharmacy.initial_state()
        line._activity = SimpleNamespace(session=SimpleNamespace(userdata=state))
        with (
            patch.object(type(line), "session", property(lambda self: self._activity.session)),
            patch.object(hosted_pharmacy._module, "publish_ui_event"),
        ):
            line.log_line("caller", "I was born March 14th, 1982, call 416-555-0199")
            spoken = line.speak("Your Rx is 4471-B. ")
        self.assertNotRegex(state["log"][0]["text"], r"\d")
        self.assertEqual(state["log"][0]["tags"], ["DOB", "PHONE"])
        self.assertIn("B as in Bravo", spoken)
        self.assertEqual(state["spoken"][0]["written"], "4471-B")

    async def test_pharmacy_uses_keyterms_and_a_bigger_budget(self):
        self.assertEqual(hosted.HostedPharmacy.llm_budget, 30)
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
            agent = hosted.HostedPharmacy()
        self.assertIn("two-minute limit", agent.instructions)
        self.assertIn("metformin", agent.stt._opts.keyterm)
        await agent.llm.aclose()

    async def test_furnace_calls_start_empty_and_dispatch_once(self):
        agent = object.__new__(hosted.HostedFurnace)
        first, second = agent.initial_state(), agent.initial_state()
        self.assertEqual(first, {"ticket": {}, "ref": None})
        self.assertIsNot(first["ticket"], second["ticket"])
        line = object.__new__(hosted_furnace.FurnaceLine)
        line.room = SimpleNamespace()
        context = SimpleNamespace(userdata=first)
        module = hosted_furnace._module
        with patch.object(module, "publish_ui_event") as publish:
            rejected = await line.update_ticket(context, callback_number="416 555 01")
            self.assertIn("callback rejected", rejected)
            self.assertIn("missing", await line.dispatch_technician(context))
            heard = await line.update_ticket(
                context,
                problem="furnace stopped, no heat",
                address="42 Maple Street, Barrie",
                callback_number="416 555 0142",
            )
            self.assertIn("4 1 6, 5 5 5, 0 1 4 2", heard)
            self.assertIn("still needed: safety", heard)
            await line.update_ticket(context, gas_smell=False, vulnerable_occupant=True)
            sent = await line.dispatch_technician(context)
            again = await line.dispatch_technician(context)
        self.assertEqual(sent, again)
        self.assertIn("within 2 hours", sent)
        self.assertRegex(first["ref"]["id"], r"^BHL-[0-9A-F]{5}$")
        self.assertEqual(second, {"ticket": {}, "ref": None})
        self.assertIn("Card", [call.args[1] for call in publish.call_args_list])

    async def test_furnace_never_dispatches_to_a_gas_smell(self):
        line = object.__new__(hosted_furnace.FurnaceLine)
        line.room = SimpleNamespace()
        data = hosted_furnace.initial_state()
        context = SimpleNamespace(userdata=data)
        with patch.object(hosted_furnace._module, "publish_ui_event"):
            note = await line.update_ticket(
                context,
                problem="no heat",
                address="9 Elm Road",
                callback_number="705 555 0199",
                gas_smell=True,
                vulnerable_occupant=False,
            )
            self.assertIn("GAS", note)
            self.assertIn("do not dispatch", await line.dispatch_technician(context))
        self.assertIsNone(data["ref"])

    async def test_furnace_uses_cloud_detector_without_local_model(self):
        self.assertEqual(
            hosted.HostedFurnace.detector_options, {"version": "v1", "local_fallback": False}
        )
        self.assertNotIn("turns", sys.modules)
        hosted.claims[ROOM] = {"id": ID}
        with (
            patch.dict(
                hosted.os.environ,
                {
                    "DEEPGRAM_API_KEY": "offline",
                    "OPENAI_API_KEY": "offline",
                    "CARTESIA_API_KEY": "offline",
                    "LIVEKIT_API_KEY": "offline",
                    "LIVEKIT_API_SECRET": "offline",
                },
            ),
            patch.object(
                hosted,
                "get_job_context",
                return_value=SimpleNamespace(room=SimpleNamespace(name=ROOM)),
            ),
        ):
            agent = hosted.HostedFurnace()
        detector = agent.turn_detection
        self.assertEqual(detector.model, "turn-detector-v1")
        self.assertIsNotNone(detector._cloud_opts)
        self.assertFalse(detector._local_fallback)
        self.assertEqual(agent.timeline.model, "turn-detector-v1")
        await agent.llm.aclose()

    async def test_mortgage_gate_tells_nods_from_barge_ins(self):
        classify = hosted_mortgage._module.classify
        for text in ("mm-hm", "Right.", "uh huh, okay", "got it", "yeah, makes sense", "Mhm"):
            self.assertEqual(classify(text), "backchannel", text)
        for text in ("wait", "stop", "hold on", "what about variable?", "no", "okay but why"):
            self.assertEqual(classify(text), "barge-in", text)
        self.assertEqual(classify(""), "noise")
        self.assertEqual(classify(" ... "), "noise")

    def mortgage_gate(self):
        module = hosted_mortgage._module
        published, pending, interrupts = [], [], []
        gate = module.OverlapGate(
            interrupt=lambda: interrupts.append(True),
            publish=published.append,
            later=lambda delay, callback: pending.append(callback),
        )
        return gate, published, pending, interrupts

    async def test_mortgage_gate_keeps_talking_through_nods_and_noise(self):
        gate, published, pending, interrupts = self.mortgage_gate()
        gate.on_agent_state("speaking", 10.0)
        gate.on_user_state("speaking", 11.0)
        gate.on_transcript("mm", False, 11.2)
        gate.on_transcript("mm-hm", True, 11.4)
        gate.on_user_state("listening", 11.5)
        self.assertTrue(gate.ignore_turn("mm-hm", 11.6))
        pending.pop()()
        gate.on_user_state("speaking", 12.0)
        gate.on_user_state("listening", 12.4)
        pending.pop()()
        self.assertFalse(interrupts)
        self.assertEqual([r["kind"] for r in published[-1]["overlaps"]], ["backchannel", "noise"])
        self.assertEqual(published[-1]["counts"], {"barge-in": 0, "backchannel": 1, "noise": 1})
        self.assertFalse(gate.ignore_turn("what does that cost", 12.5))

    async def test_mortgage_gate_stops_on_a_real_word_and_records_the_cut(self):
        gate, published, pending, interrupts = self.mortgage_gate()
        gate.on_agent_state("speaking", 10.0)
        gate.on_user_state("speaking", 11.0)
        gate.on_transcript("uh", False, 11.1)
        self.assertFalse(interrupts)
        gate.on_transcript("uh wait", False, 11.35)
        gate.on_transcript("uh wait stop", False, 11.5)
        self.assertEqual(interrupts, [True])
        gate.on_agent_state("listening", 11.42)
        gate.on_cut("A fixed rate means", "A fixed rate means your payment never changes.")
        gate.on_user_state("listening", 11.8)
        pending.pop()()
        record = published[-1]["overlaps"][-1]
        self.assertEqual(record["kind"], "barge-in")
        self.assertEqual((record["decide_ms"], record["stop_ms"]), (350, 420))
        self.assertEqual(
            record["cut"],
            {"heard": "A fixed rate means", "unheard": "your payment never changes."},
        )
        # A late cut fills the newest barge-in that has none.
        record_free = dict(record, cut=None)
        gate.records[-1] = record_free
        gate.on_cut("Variable follows prime", "Variable follows prime, so it moves.")
        self.assertEqual(published[-1]["overlaps"][-1]["cut"]["unheard"], "so it moves.")

    async def test_mortgage_resumed_speech_is_a_new_overlap(self):
        gate, published, pending, _ = self.mortgage_gate()
        gate.on_agent_state("speaking", 1.0)
        gate.on_user_state("speaking", 2.0)
        gate.on_user_state("listening", 2.3)
        stale = pending.pop()
        gate.on_user_state("speaking", 2.5)
        stale()
        self.assertIsNotNone(gate.current)
        gate.on_user_state("listening", 2.9)
        pending.pop()()
        self.assertIsNone(gate.current)
        self.assertEqual(len(published[-1]["overlaps"]), 1)

    async def test_mortgage_turn_handling_and_payments(self):
        self.assertEqual(
            hosted.HostedMortgage.turn_handling["interruption"],
            {"mode": "vad", "min_words": hosted_mortgage._module.GATE_MIN_WORDS},
        )
        self.assertNotIn("min_words", hosted.HostedGuard.turn_handling["interruption"])
        payment = hosted_mortgage._module.monthly_payment
        self.assertEqual(payment(1.89, 480_000, 25), 2007)
        self.assertEqual(payment(4.19), 2464)
        props = hosted_mortgage._module.renewal_props("3-year fixed")
        self.assertEqual(props["selected"], "3-year fixed")
        self.assertEqual(len(props["options"]), 3)
        agent = hosted_mortgage.MortgageAdvisor(SimpleNamespace())
        with patch.object(hosted_mortgage._module, "publish_ui_event") as publish:
            result = await agent.compare_option(SimpleNamespace(), "5-year variable")
        self.assertIn("$2,518 a month", result)
        self.assertEqual(publish.call_args.args[1:3], ("Renewal", "update"))

    def router_agent(self):
        """A hosted router agent for one call, with a fake room and vision client."""
        hosted.claims[ROOM] = {"id": ID, "seconds": 120, "deadline": time.time() + 120}
        room = SimpleNamespace(name=ROOM, remote_participants={})
        with (
            patch.dict(
                hosted.os.environ,
                {
                    "DEEPGRAM_API_KEY": "offline",
                    "OPENAI_API_KEY": "offline",
                    "CARTESIA_API_KEY": "offline",
                    "VOICEGW_COLLECTOR_URL": "http://localhost:8080",
                    "VOICEGW_API_KEY": "offline",
                },
            ),
            patch.object(hosted, "get_job_context", return_value=SimpleNamespace(room=room)),
        ):
            agent = hosted.HostedRouter()
            agent.sink = hosted.PlaygroundSink(ID)
        return agent

    def test_router_playbook_reads_lights_most_severe_first(self):
        def reading(visible=True, **lights):
            base = {"power": "solid_green", "internet": "solid_green", "wifi": "solid_green"}
            return {"router_visible": visible, "lights": {**base, "lan": "off", **lights}}

        cases = [
            (reading(visible=False), "no_router"),
            (reading(power="off", internet="off"), "no_power"),
            (reading(power="solid_red"), "hardware_fault"),
            (reading(power="blinking_white"), "booting"),
            (reading(internet="off", wifi="off"), "no_line"),
            (reading(internet="blinking_amber"), "no_sync"),
            (reading(internet="solid_red"), "outage"),
            (reading(wifi="off"), "wifi_off"),
            (reading(internet="blinking_green"), "healthy"),
            (reading(internet="not_visible"), "unclear"),
        ]
        for value, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(hosted_router.diagnose(value), expected)
                self.assertIn(expected, hosted_router._module.DIAGNOSES)

    def test_router_strip_puts_every_frame_in_one_image(self):
        from io import BytesIO

        from PIL import Image

        frames = [Image.new("RGB", (640, 480), color) for color in ("red", "black", "red")]
        strip = Image.open(BytesIO(hosted_router._module.contact_strip(frames, (160, 120), 70)))
        self.assertEqual(strip.size, (480, 120))
        self.assertGreater(strip.getpixel((80, 60))[0], 200)
        self.assertLess(strip.getpixel((240, 60))[0], 60)

    async def test_router_looks_only_when_asked_and_bills_the_call(self):
        agent = self.router_agent()
        module = hosted_router._module
        context = MagicMock()
        context.session.say.return_value = MagicMock(done=MagicMock(return_value=True))
        with patch.object(module, "camera_track", return_value=None):
            self.assertIn("camera is off", await agent.look_at_camera(context, "first look"))
        self.assertFalse(agent._looks)

        from PIL import Image

        reading = {
            "router_visible": True,
            "lights": {
                "power": "solid_green",
                "internet": "blinking_amber",
                "wifi": "solid_green",
                "lan": "glowing",
            },
            "label": "Network: Northline-4F2A",
            "note": "",
        }
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100),
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(reading)))],
        )
        agent._vision = MagicMock()
        agent._vision.chat.completions.create = AsyncMock(return_value=response)
        frames = [Image.new("RGB", (64, 48))] * 3
        with (
            patch.object(module, "camera_track", return_value=object()),
            patch.object(module, "sample_frames", new_callable=AsyncMock, return_value=frames),
            patch.object(module, "publish_ui_event") as publish,
            patch.object(agent, "_publish_look") as look_event,
            patch.object(hosted, "control", new_callable=AsyncMock) as control,
        ):
            first = await agent.look_at_camera(context, "check the lights")
            reused = await agent.look_at_camera(context, "check again")
            await asyncio.sleep(0)
        self.assertIn("No sync with the network", first)
        self.assertIn("a moment ago", reused)
        self.assertEqual(agent._vision.chat.completions.create.await_count, 1)
        sent = agent._vision.chat.completions.create.await_args.kwargs
        self.assertFalse(sent["store"])
        self.assertEqual(sent["messages"][1]["content"][1]["image_url"]["detail"], "low")
        look = look_event.call_args.args[0]
        self.assertEqual((look["n"], look["diagnosis"], look["frames"]), (1, "no_sync", 3))
        self.assertEqual(look["lights"]["lan"], "not_visible")
        stats = publish.call_args.kwargs["props"]
        self.assertEqual((stats["looks"], stats["ticket"]), (1, "open"))
        # 1,000 input tokens at $0.40/M plus 100 output at $1.60/M is 560 micro-dollars.
        control.assert_any_await("usage", ID, service="openai", microusd=560)
        agent._looks = [dict(look, focus="label", n=i) for i in range(6)]
        with patch.object(module, "camera_track", return_value=object()):
            self.assertIn("limit", await agent.look_at_camera(context, "again"))
        await agent.llm.aclose()

    async def test_router_calls_keep_their_own_looks(self):
        first = self.router_agent()
        second = self.router_agent()
        first._looks.append({"n": 1})
        self.assertEqual(second._looks, [])
        self.assertIsNot(first._vision, second._vision)
        self.assertEqual(hosted.HostedRouter.llm_budget, 20)
        self.assertGreater(hosted_router.vision_cost(None), 0)
        for agent in (first, second):
            await agent.llm.aclose()
            await agent._vision.close()

    def rebook_agent(self, room=ROOM):
        hosted.claims[room] = {"id": ID}
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
                return_value=SimpleNamespace(room=SimpleNamespace(name=room)),
            ),
        ):
            return hosted.HostedRebook()

    async def test_rebook_calls_get_their_own_failover_stack_and_state(self):
        first, second = self.rebook_agent(), self.rebook_agent("playground-second")
        await asyncio.sleep(0)
        self.assertIsInstance(first.llm, hosted_rebook._module.llm.FallbackAdapter)
        self.assertIsInstance(first.tts, hosted_rebook._module.tts.FallbackAdapter)
        self.assertIsNot(first.llm, second.llm)
        self.assertIsNot(first.outage, second.outage)
        self.assertIs(first.initial_state(), first.state)
        self.assertIsNot(first.state, second.state)
        first.outage.start("llm")
        self.assertFalse(second.outage.down("llm"))
        for agent in (first, second):
            for component in agent.llm._llm_instances:
                await component.aclose()

    async def test_rebook_search_runs_in_background_and_books_the_pick(self):
        module = hosted_rebook._module
        agent = module.FlightRebooker(SimpleNamespace())

        @asynccontextmanager
        async def filler(*_args, **_kwargs):
            yield

        context = SimpleNamespace(update=AsyncMock(), with_filler=filler)
        with (
            patch.object(module, "publish_ui_event") as publish,
            patch.object(module, "SEARCH_SECONDS", 0),
        ):
            self.assertIn("Search first", await agent.rebook(context, "A"))
            search = asyncio.create_task(agent.search_flights(context))
            await asyncio.sleep(0)
            # The search has released control; a policy answer lands meanwhile.
            self.assertEqual(agent.state["search"], "running")
            context.update.assert_awaited_once()
            self.assertIn("bags", (await agent.check_policy(context, "bags")).lower())
            self.assertIn("C: Borealis 102", await search)
            self.assertTrue(agent.outage.down("llm"))
            self.assertFalse(agent.outage.down("tts"))
            self.assertIn("Borealis 102", await agent.rebook(context, "C"))
            self.assertIn("already rebooked", await agent.search_flights(context))
            self.assertIn("now failing", await agent.simulate_outage(context, "voice"))
            self.assertIn("already", await agent.simulate_outage(context, "voice"))
        self.assertEqual(agent.state["booking"]["status"], "rebooked")
        self.assertEqual(agent.state["booking"]["seat"], "14A window")
        lanes = [event["lane"] for event in agent.state["events"]]
        self.assertEqual(lanes.count("tts"), 1)
        texts = " ".join(event["text"] for event in agent.state["events"])
        self.assertIn("answered while the search keeps running", texts)
        snapshot = publish.call_args.args[2]
        self.assertLess(len(json.dumps(snapshot)), 16384)
        self.assertEqual(module.new_state()["events"], [])

    async def test_rebook_outage_fails_over_and_recovers_without_network(self):
        module = hosted_rebook._module
        agent = module.FlightRebooker(SimpleNamespace())

        class Backup(module.llm.LLM):
            def chat(self, *, chat_ctx, tools=None, conn_options=None, **_kwargs):
                return BackupStream(
                    self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options
                )

        class BackupStream(module.llm.LLMStream):
            async def _run(self):
                self._event_ch.send_nowait(
                    module.llm.ChatChunk(id="b", delta=module.llm.ChoiceDelta(content="ok"))
                )

        with patch.dict(os.environ, {"OPENAI_API_KEY": "offline", "CARTESIA_API_KEY": "x"}):
            primary = module.FlakyLLM(outage=agent.outage, model="gpt-4o-mini")
            voice = module.build_tts(agent.outage)
        adapter = module.llm.FallbackAdapter([primary, Backup()], attempt_timeout=1)
        agent.watch(adapter, voice)
        agent.outage.start("llm")
        chat = module.llm.ChatContext.empty()
        chat.add_message(role="user", content="hello")
        with patch.object(module, "publish_ui_event"):
            async with adapter.chat(chat_ctx=chat) as stream:
                text = "".join([c.delta.content async for c in stream if c.delta])
            self.assertEqual(text, "ok")
            self.assertEqual(agent.state["providers"]["llm"]["serving"], "backup")
            self.assertIn("gpt-4.1-nano took the next request", agent.state["events"][-1]["text"])
            # A passed recovery probe hands traffic back; the voice lane reports alike.
            adapter.emit("llm_availability_changed", SimpleNamespace(llm=primary, available=True))
            self.assertEqual(agent.state["providers"]["llm"]["serving"], "primary")
            cartesia_voice = voice._tts_instances[0]
            voice.emit(
                "tts_availability_changed", SimpleNamespace(tts=cartesia_voice, available=False)
            )
            self.assertEqual(agent.state["providers"]["tts"]["serving"], "backup")
        with self.assertRaises(module.APIConnectionError):
            primary.chat(chat_ctx=chat)
        with self.assertRaises(module.APIConnectionError):
            agent.outage.start("tts")
            cartesia_voice.stream()
        await adapter.aclose()
        await voice.aclose()

    def returns_desk(self):
        desk = hosted_returns.ReturnsDesk(SimpleNamespace())
        state = hosted_returns.initial_state()
        context = SimpleNamespace(userdata=state)
        return desk, state, context

    async def test_returns_tools_enforce_verification_and_policy(self):
        first, second = hosted_returns.initial_state(), hosted_returns.initial_state()
        self.assertIsNot(first["verified"], second["verified"])
        desk, state, context = self.returns_desk()
        with patch.object(hosted_returns._module, "publish_ui_event") as publish:
            self.assertIn("refused", await desk.process_return(context, "FW4821", "refund"))
            self.assertIn("not verified", await desk.verify_order(context, "FW4821", "90210"))
            self.assertIn("verified FW4821", await desk.verify_order(context, "4 8 2 1", "60614"))
            self.assertIn("refused", await desk.process_return(context, "FW4821", "refund"))
            self.assertIn("refund", await desk.check_return_options(context, "fw-4821", False))
            issued = await desk.process_return(context, "FW4821", "refund")
            again = await desk.process_return(context, "FW4821", "refund")
            await desk.verify_order(context, "FW6017", "60614")
            self.assertIn("final sale", await desk.check_return_options(context, "FW6017", False))
            self.assertIn("refused", await desk.process_return(context, "FW6017", "refund"))
            self.assertIn("replacement", await desk.check_return_options(context, "FW6017", True))
        rma = state["returns"]["FW4821"]["rma"]
        self.assertRegex(rma, r"^RMA-[0-9A-F]{6}$")
        self.assertIn(rma, issued)
        self.assertEqual(issued, again)
        self.assertEqual(second, hosted_returns.initial_state())
        self.assertIsNotNone(desk.qa.items["verified"])
        self.assertIsNotNone(desk.qa.items["resolution"])
        self.assertEqual([f["kind"] for f in desk.qa.flags], ["guard"] * 3)
        boards = [c.kwargs["props"] for c in publish.call_args_list if c.args[1] == "QaBoard"]
        self.assertEqual(boards[-1]["score"], 33)

    async def test_returns_policy_windows(self):
        options = hosted_returns._module.return_options
        lamp = {"days": 41, "final_sale": False}
        self.assertEqual(options(lamp, False)[0], ["store_credit"])
        self.assertEqual(options(lamp, True)[0], ["store_credit"])
        self.assertEqual(options({"days": 75, "final_sale": False}, False)[0], [])
        self.assertEqual(options({"days": 30, "final_sale": False}, False)[0][0], "refund")

    async def test_returns_grader_ticks_flags_and_whispers_once(self):
        usage = []
        qa = hosted_returns._module.QaSupervisor(
            SimpleNamespace(), lambda: "{}", lambda i, o: usage.append((i, o)), max_grades=2
        )
        grade = {
            "evidence": ["disclosure", "empathy", "made_up"],
            "sentiment": -3,
            "violation": "off_policy_promise",
            "quote": "I can refund the throw as an exception",
            "coaching": "Final sale cannot be refunded; correct yourself.",
        }
        reply = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=500, completion_tokens=60),
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(grade)))],
        )
        qa._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=reply))),
            close=AsyncMock(),
        )
        line = lambda role, text: SimpleNamespace(role=role, text_content=text)  # noqa: E731
        with patch.object(hosted_returns._module, "publish_ui_event"):
            qa.observe(line("user", "Just refund the throw, it's garbage."))
            qa.observe(line("assistant", "I can refund the throw as an exception."))
            await qa._running
            for _ in range(3):
                qa.observe(line("assistant", "Anything else?"))
                await qa._running
            self.assertEqual(qa.grades, 2)
            self.assertEqual(qa.take_whisper(), grade["coaching"])
            self.assertIsNone(qa.take_whisper())
        self.assertIsNotNone(qa.items["disclosure"])
        self.assertIsNone(qa.items["policy"])
        self.assertEqual([point["score"] for point in qa.sentiment], [-1.0])
        self.assertTrue(qa.flags[-1]["whispered"])
        self.assertEqual(qa.score(), max(0, round(100 * 2 / 6) - 30))
        self.assertEqual(usage, [(500, 60), (500, 60)])
        await qa.aclose()
        self.assertEqual(
            hosted_returns.grade_cost(1_000_000, 1_000_000), hosted_returns.Decimal("2.00")
        )

    async def test_returns_grades_are_capped_and_billed_to_the_call(self):
        hosted.claims[ROOM] = {"id": ID, "seconds": 60}
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
            agent = hosted.HostedReturns()
        self.assertEqual(agent.qa.max_grades, 7)
        agent.record_qa_usage(1000, 100)  # before the sink exists: ignored, not raised
        agent.sink = SimpleNamespace(records={}, report=AsyncMock())
        agent.record_qa_usage(1000, 100)
        await asyncio.sleep(0)
        ((provider, cost),) = agent.sink.records.values()
        self.assertEqual((provider, cost), ("openai", hosted_returns.Decimal("0.00056")))
        agent.sink.report.assert_awaited_once()
        await agent.llm.aclose()

    async def test_outage_calls_get_their_own_card_line_and_stt(self):
        first, second = hosted_outage.initial_state(), hosted_outage.initial_state()
        self.assertEqual(first["readings"], [])
        self.assertIsNone(first["ticket"])
        self.assertIsNot(first["readings"], second["readings"])
        for name in hosted_outage._HELPERS:
            self.assertNotIn(name, sys.modules)
        self.assertEqual(hosted.HostedOutage.llm_budget, 20)
        rooms = [SimpleNamespace(name=ROOM), SimpleNamespace(name="playground-second")]
        hosted.claims.update({ROOM: {"id": ID}, "playground-second": {"id": "second"}})
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
            a, b = hosted.HostedOutage(), hosted.HostedOutage()
        self.assertIsNot(a.line, b.line)
        self.assertIsNot(a.stt, b.stt)
        # The outage STT formats numbers so readings compare with the card.
        self.assertTrue(a.stt._opts.smart_format)
        await a.llm.aclose()
        await b.llm.aclose()

    async def test_outage_line_sits_between_the_room_and_the_agent(self):
        from livekit import rtc
        from livekit.agents.voice.io import AudioInput

        class Mic(AudioInput):
            def __init__(self):
                super().__init__(label="mic")
                self.sent = 0

            async def __anext__(self):
                self.sent += 1
                return rtc.AudioFrame(bytes(480), 24000, 1, 240)

        agent = object.__new__(hosted.HostedOutage)
        agent.room = MagicMock()
        agent.line = hosted_outage._module.PhoneLine(seed=1)
        mic = Mic()
        session = SimpleNamespace(
            input=SimpleNamespace(audio=mic),
            userdata=hosted_outage.initial_state(),
            on=MagicMock(),
        )
        with (
            patch.object(hosted.HostedOutage, "session", session),
            patch.object(hosted_outage._module, "publish_ui_event") as publish,
        ):
            agent.publish_initial()
            agent.publish_initial()
        wrapped = session.input.audio
        self.assertIsNot(wrapped, mic)
        self.assertIs(wrapped.source, mic)
        self.assertEqual(len(session.userdata["readings"]), 1)
        self.assertEqual(publish.call_args.args[1], "OutageLine")
        agent.line.set_line("landline", False)
        frame = await wrapped.__anext__()
        self.assertEqual(frame.sample_rate, 24000)
        self.assertGreater(agent.line.stats.packets, 0)

    async def test_cancel_calls_get_their_own_account(self):
        self.assertNotIn("policy", sys.modules)
        agent = object.__new__(hosted.HostedCancel)
        first, second = agent.initial_state(), agent.initial_state()
        self.assertIsNot(first["account"], second["account"])
        self.assertIsNot(first["log"], second["log"])
        self.assertIn("two-minute limit", hosted_cancel.instructions())

    def cancel_line(self, state):
        line = hosted_cancel.CancelLine(SimpleNamespace())
        session = MagicMock(userdata=state)
        return line, session

    async def test_cancel_policy_cancels_without_the_model(self):
        from livekit.agents import StopResponse, llm

        state = hosted_cancel.initial_state()
        line, session = self.cancel_line(state)
        turn = llm.ChatContext.empty()
        with (
            patch.object(type(line), "session", property(lambda self: session)),
            patch.object(hosted_cancel._module, "publish_ui_event") as publish,
        ):
            message = llm.ChatMessage(role="user", content=["I want to cancel"])
            await line.on_user_turn_completed(turn, message)
            context = SimpleNamespace(userdata=state)
            await line.note_reason(context, "price")
            self.assertTrue((await line.make_offer(context)).startswith("approved"))
            with self.assertRaises(StopResponse):
                no = llm.ChatMessage(role="user", content=["No thanks, just cancel"])
                await line.on_user_turn_completed(turn, no)
        said = session.say.call_args
        self.assertIn("is cancelled", said.args[0])
        self.assertFalse(said.kwargs["allow_interruptions"])
        props = publish.call_args.args[2]
        self.assertEqual(publish.call_args.args[1], "Cancel")
        self.assertEqual(props["clock"]["state"], "enforced")
        self.assertRegex(props["outcome"]["ref"], r"^CX-[3479ACFHKMRX]{3}-[3479ACFHKMRX]{3}$")

    async def test_cancel_tts_speaks_the_disclosure_and_drops_unapproved_deals(self):
        state = hosted_cancel.initial_state()
        hosted_cancel.policy.observe_caller(state, "cancel please")
        hosted_cancel.policy.note_reason(state, "switching")
        line, session = self.cancel_line(state)
        heard = []

        async def fake_tts(self, text, model_settings):
            async for chunk in text:
                heard.append(chunk)
            yield b"frame"

        async def reply():
            for chunk in ["Sorry to see you go. I can do 40", "% off for a year. Done?"]:
                yield chunk

        with (
            patch.object(type(line), "session", property(lambda self: session)),
            patch.object(hosted_cancel._module, "publish_ui_event"),
            patch.object(hosted_cancel._module.Agent, "tts_node", fake_tts),
        ):
            async for _ in line.tts_node(reply(), None):
                pass
            spoken = "".join(heard)
            self.assertNotIn("40", spoken)
            self.assertIn("Sorry to see you go.", spoken)
            state["reason"] = "price"
            hosted_cancel.policy.make_offer(state)
            heard.clear()
            async for _ in line.tts_node(reply(), None):
                pass
        spoken = "".join(heard)
        self.assertTrue(spoken.startswith(hosted_cancel.policy.DISCLOSURE))
        # The approved offer is half off; the model's 40% is still not spoken.
        self.assertNotIn("40", spoken)
        self.assertEqual(state["log"][-2]["rule"], "OFFER.DISCLOSE")
        self.assertEqual(state["log"][-1]["rule"], "SPEECH.SCREEN")

    async def test_cancel_uses_sonic_3_and_a_bigger_budget(self):
        self.assertEqual(hosted.HostedCancel.llm_budget, 20)
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
            agent = hosted.HostedCancel()
        self.assertEqual(agent.tts.model, "sonic-3")
        self.assertIn("two-minute limit", agent.instructions)
        await agent.llm.aclose()

    async def test_postop_calls_get_their_own_protocol(self):
        self.assertNotIn("protocol", sys.modules)
        agent = object.__new__(hosted.HostedPostop)
        first, second = agent.initial_state(), agent.initial_state()
        self.assertIsNot(first["answers"], second["answers"])
        call = hosted_postop.CheckInCall(SimpleNamespace())
        context = SimpleNamespace(userdata=first)
        with patch.object(hosted_postop._module, "publish_ui_event") as publish:
            self.assertIn("refused", await call.finish_check_in(context))
            await call.record_breathing(context, False, "breathing's fine")
            await call.record_calf(context, False, "no")
            fever = await call.record_temperature(context, 102.2, "F", False, "one oh two two")
            self.assertIn("Rule N2 fired", fever)
            await call.record_wound(context, False, "clear", False, "a bit of clear fluid")
            await call.record_pain(context, 5, True, "five, the pills help")
            said = await call.finish_check_in(context)
        self.assertTrue(said.startswith("Outcome NURSE"))
        self.assertIsNone(second["outcome"])
        component, props = publish.call_args.args[1:3]
        self.assertEqual(component, "PostOp")
        self.assertEqual(props["tier"], "nurse")
        self.assertRegex(props["outcome"]["ref"], r"^NL-\d{4}-[0-9A-F]{4}$")
        self.assertEqual(len(props["handoff"]["assessment"]), 5)

    async def test_postop_emergency_ends_the_protocol(self):
        call = hosted_postop.CheckInCall(SimpleNamespace())
        context = SimpleNamespace(userdata=hosted_postop.initial_state())
        with patch.object(hosted_postop._module, "publish_ui_event"):
            said = await call.record_breathing(context, True, "it's a little tight")
            later = await call.record_calf(context, False, "no")
        self.assertIn("911", said)
        self.assertIn("911", later)
        self.assertIsNone(context.userdata["answers"]["calf"])

    async def test_postop_uses_sonic_3_and_a_per_call_greeting(self):
        self.assertEqual(hosted.HostedPostop.llm_budget, 26)
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
            agent = hosted.HostedPostop()
        self.assertIn("two-minute limit", agent.instructions)
        self.assertEqual(agent.tts.model, "sonic-3")
        self.assertTrue(agent.stt._opts.smart_format)
        state = agent.initial_state()
        with patch.object(
            type(agent), "session", property(lambda self: SimpleNamespace(userdata=state))
        ):
            self.assertIn(state["patient"]["name"], agent.greeting)
        self.assertIn("made-up hospital", hosted_postop.greeting(state))
        await agent.llm.aclose()

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

    def fraud_desk(self):
        room = SimpleNamespace(name=ROOM)
        hosted.claims[ROOM] = {"id": ID, "seconds": 60}
        with (
            patch.dict(
                hosted.os.environ,
                {
                    "DEEPGRAM_API_KEY": "offline",
                    "OPENAI_API_KEY": "offline",
                    "CARTESIA_API_KEY": "offline",
                },
            ),
            patch.object(hosted, "get_job_context", return_value=SimpleNamespace(room=room)),
        ):
            return hosted.HostedFraud()

    async def test_fraud_handoffs_use_the_call_clients_and_a_second_voice(self):
        guard = self.fraud_desk()
        self.addAsyncCleanup(guard.llm.aclose)
        verifier = guard.make_verifier()
        self.assertIs(verifier.llm, guard.llm)
        self.assertIs(verifier.tts, guard.tts)
        with patch.dict(hosted.os.environ, {"CARTESIA_API_KEY": "offline"}):
            desk = guard.make_fraud_desk(hosted_fraud.initial_state() | {"handoff": []})
        self.assertIs(desk.stt, guard.stt)
        self.assertIs(desk.llm, guard.llm)
        self.assertIsNot(desk.tts, guard.tts)
        self.assertNotEqual(desk.tts._opts.voice, guard.tts._opts.voice)
        self.assertEqual(guard.tts._opts.model, "sonic-3")

    async def test_fraud_handoffs_cannot_reset_the_call_caps(self):
        guard = object.__new__(hosted.HostedFraud)
        guard.approval = {"seconds": 60}
        guard._llm_requests = 14
        guard._tts_bytes = 0
        guard._finish = AsyncMock()
        context = SimpleNamespace(to_dict=dict)
        for agent in (
            hosted_fraud.HostedVerify(guard, SimpleNamespace()),
            hosted_fraud.HostedFraudDesk(guard, SimpleNamespace(), {"handoff": []}),
        ):
            chunks = [chunk async for chunk in agent.llm_node(context, [], None)]
            self.assertEqual(chunks, [])
        await asyncio.sleep(0)
        self.assertEqual(guard._llm_requests, 16)
        self.assertEqual(guard._finish.await_count, 2)
        guard._finish.reset_mock()

        async def text():
            yield "a" * 1900
            yield "b" * 200

        async def fake_tts(_agent, bounded, _settings):
            async for chunk in bounded:
                yield chunk

        with patch.object(hosted_fraud.FraudDesk, "tts_node", fake_tts):
            desk = hosted_fraud.HostedFraudDesk(guard, SimpleNamespace(), {"handoff": []})
            spoken = [chunk async for chunk in desk.tts_node(text(), None)]
        self.assertEqual(sum(map(len, spoken)), 1900)
        await asyncio.sleep(0)
        guard._finish.assert_awaited_once()

    async def test_fraud_calls_start_locked_with_their_own_code(self):
        agent = object.__new__(hosted.HostedFraud)
        first, second = agent.initial_state(), agent.initial_state()
        self.assertFalse(first["verified"])
        self.assertEqual(set(first["tools"].values()), {"locked"})
        self.assertIsNot(first["flags"], second["flags"])
        self.assertRegex(first["code"], r"^\d{6}$")
        self.assertIn("simulated", hosted.HostedFraud.greeting)


class FakeSession:
    def __init__(self):
        self.handlers = {}
        self.update_agent = MagicMock()

    def on(self, name, handler):
        self.handlers[name] = handler

    def emit(self, name, **fields):
        self.handlers[name](SimpleNamespace(**fields))


def message(role, text="", **metrics):
    return SimpleNamespace(role=role, text_content=text, metrics=metrics)


class InterviewDemo(unittest.IsolatedAsyncioTestCase):
    def desk(self, order=("cascade", "duplex"), **options):
        session = FakeSession()
        room = SimpleNamespace(local_participant=SimpleNamespace(publish_data=AsyncMock()))
        built = []

        def build(architecture, label, question):
            built.append((architecture, label, question))
            return SimpleNamespace(architecture=architecture)

        desk = hosted_interview.InterviewDesk(session, room, order, build=build, **options)
        return desk, session, room, built

    async def events(self, room):
        await asyncio.sleep(0)
        sent = [
            json.loads(call.args[0]) for call in room.local_participant.publish_data.await_args_list
        ]
        return [(event["component"], event["props"]) for event in sent]

    def answer(self, session, text="I led the migration."):
        session.emit("user_state_changed", old_state="listening", new_state="speaking")
        session.emit("user_state_changed", old_state="speaking", new_state="listening")
        session.emit(
            "conversation_item_added",
            item=message("user", text, end_of_turn_delay=0.5, transcription_delay=0.2),
        )
        session.emit("agent_state_changed", old_state="thinking", new_state="speaking")

    async def test_order_is_shared_with_the_site_and_covers_both(self):
        self.assertEqual(hosted_interview.order_for(ID), ("duplex", "cascade"))
        self.assertEqual(hosted_interview.order_for(ID[:-1] + "6"), ("cascade", "duplex"))
        self.assertEqual(hosted_interview.round_seconds(120), hosted_interview.ROUND_SECONDS)
        self.assertEqual(hosted_interview.round_seconds(30), 20.0)
        with self.assertRaises(ValueError):
            self.desk(order=("cascade", "cascade"))

    async def test_rounds_hand_off_then_reveal(self):
        done = AsyncMock()
        desk, session, room, built = self.desk(on_done=done)
        desk.first_agent()
        self.assertEqual(built[0][:2], ("cascade", "A"))
        # The opening line has no answer before it, so it is not a timed turn.
        session.emit("conversation_item_added", item=message("assistant", "Hi"))
        for _ in range(2):
            self.answer(session)
            session.emit(
                "conversation_item_added",
                item=message(
                    "assistant", "Nice", e2e_latency=1.1, llm_node_ttft=0.3, tts_node_ttfb=0.2
                ),
            )
        session.update_agent.assert_called_once()
        self.assertEqual(built[1][:2], ("duplex", "B"))
        events = await self.events(room)
        turns = [props for name, props in events if name == "Turn"]
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["total_ms"], 1100)
        self.assertEqual(turns[0]["stages"], {"endpoint": 500, "stt": 200, "llm": 300, "tts": 200})
        # Duplex turns have no stages; a reply that starts over the candidate is an overlap.
        session.emit("user_state_changed", old_state="listening", new_state="speaking")
        session.emit("agent_state_changed", old_state="listening", new_state="speaking")
        session.emit("conversation_item_added", item=message("user", "It went down twice."))
        session.emit("conversation_item_added", item=message("assistant", "Mm-hm"))
        self.answer(session)
        session.emit("conversation_item_added", item=message("assistant", "Thanks"))
        await asyncio.sleep(0)
        done.assert_awaited_once()
        events = await self.events(room)
        turns = [props for name, props in events if name == "Turn"]
        self.assertTrue(turns[2]["overlap"])
        self.assertEqual(turns[2]["stages"], {})
        self.assertEqual(turns[2]["total_ms"], 0)
        self.assertIn(("Reveal", {"A": "cascade", "B": "duplex"}), events)
        self.assertEqual(events[-2][1]["status"], "done")
        desk.close()

    async def test_slow_round_moves_on_and_replies_are_capped(self):
        desk, session, _, built = self.desk(round_seconds=0)
        desk.first_agent()
        self.answer(session)
        session.emit("conversation_item_added", item=message("assistant", "Go on"))
        session.update_agent.assert_called_once()
        desk.close()
        desk, session, _, _ = self.desk()
        for _ in range(hosted_interview._module.MAX_REPLIES_PER_ROUND):
            session.emit("conversation_item_added", item=message("assistant", "Hello?"))
        session.update_agent.assert_called_once()
        desk.close()

    async def test_cascade_interviewer_stops_the_call_past_its_cap(self):
        stop = AsyncMock()
        spawned = []
        with patch.dict(
            os.environ, {"OPENAI_API_KEY": "t", "DEEPGRAM_API_KEY": "t", "CARTESIA_API_KEY": "t"}
        ):
            build = hosted_interview.bounded_builder(
                stop, lambda coro: spawned.append(asyncio.ensure_future(coro))
            )
            agent = build("cascade", "A", "Why?")
            duplex = build("duplex", "B", "Why?")
        self.assertEqual(duplex.architecture, "duplex")
        agent._llm_requests = hosted_interview.CASCADE_LLM_REQUESTS
        chunks = [chunk async for chunk in agent.llm_node(SimpleNamespace(), [], None)]
        self.assertEqual(chunks, [])
        await asyncio.gather(*spawned)
        stop.assert_awaited_once()
        for _ in range(hosted_interview.DUPLEX_RESPONSES + 1):
            duplex._count({"type": "response.event", "event": {"type": "response.created"}})
        await asyncio.gather(*spawned)
        self.assertEqual(stop.await_count, 2)


if __name__ == "__main__":
    unittest.main()
