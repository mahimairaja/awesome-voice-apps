"""Offline tests for the hosted medical bill explainer; never call provider APIs."""

import asyncio
import io
import json
import time
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import hosted
import hosted_bill
from livekit import rtc
from PIL import Image, UnidentifiedImageError

ID = "598cd768-86d4-42a1-bb44-adc44fba4207"
ROOM = f"playground-{ID}"
module = hosted_bill._module
SAMPLES = Path(module.__file__).parent / "samples"


def line(date, code, billed, allowed, paid, owe, description="Service", remark=""):
    return {
        "date": date,
        "code": code,
        "description": description,
        "billed": billed,
        "allowed": allowed,
        "plan_paid": paid,
        "you_owe": owe,
        "remark": remark,
    }


# What the vision model should return for each sample in samples/.
URGENT_CARE = {
    "readable": True,
    "kind": "eob",
    "provider": "Brightwater Urgent Care",
    "network": "in",
    "lines": [
        line("09/14/2026", "99214", 245.00, 162.40, 112.40, 50.00, "Office visit", "Copay"),
        line("09/14/2026", "85025", 58.00, 21.30, 17.04, 4.26, "Complete blood count (CBC)"),
        line("09/14/2026", "80053", 96.00, 28.75, 23.00, 5.75, "Comprehensive metabolic panel"),
        line("09/14/2026", "85025", 58.00, 21.30, 17.04, 4.26, "Complete blood count (CBC)"),
        line("09/14/2026", "87880", 64.00, 24.10, 19.28, 4.82, "Rapid strep test"),
    ],
    "total_you_owe": 69.09,
}
IMAGING = {
    "readable": True,
    "kind": "provider_bill",
    "provider": "Lakeshore Imaging Center",
    "network": "in",
    "lines": [
        line("09/03/2026", "70553", 2850.00, 1120.00, 896.00, 1954.00, "MRI brain"),
        line("09/03/2026", "A9579", 180.00, 64.00, 51.20, 12.80, "Gadolinium contrast"),
        line("09/03/2026", "70553-26", 410.00, 152.00, 121.60, 30.40, "Radiologist reading"),
    ],
    "total_you_owe": 1997.20,
}
PHYSIO = {
    "readable": True,
    "kind": "eob",
    "provider": "Northgate Physical Therapy",
    "network": "in",
    "lines": [
        line("09/21/2026", "97161", 180.00, 98.00, 0.00, 98.00, "PT evaluation"),
        line("09/21/2026", "97110", 85.00, 42.00, 0.00, 42.00, "Therapeutic exercise"),
        line("09/21/2026", "97140", 75.00, 38.00, 0.00, 38.00, "Manual therapy"),
    ],
    "total_you_owe": 178.00,
}


class FakeReader:
    """A byte stream reader that yields the given chunks."""

    def __init__(self, chunks, mime="image/png", size=None, name="bill.png"):
        self.chunks = list(chunks)
        self.info = SimpleNamespace(
            name=name, mime_type=mime, size=sum(map(len, self.chunks)) if size is None else size
        )
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed or not self.chunks:
            raise StopAsyncIteration
        return self.chunks.pop(0)

    def close(self):
        self.closed = True


def visitor(kind=rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD):
    return SimpleNamespace(kind=kind)


class HostedBillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        hosted.claims.clear()

    def agent(self):
        hosted.claims[ROOM] = {"id": ID, "seconds": 120, "deadline": time.time() + 120}
        room = SimpleNamespace(
            name=ROOM,
            remote_participants={"visitor": visitor(), "other-agent": visitor(4)},
        )
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
            agent = hosted.HostedBill()
            agent.sink = hosted.PlaygroundSink(ID)
        return agent

    async def close(self, *agents):
        for agent in agents:
            await agent.llm.aclose()
            await agent._vision.close()

    def test_audit_catches_the_duplicate_the_balance_bill_and_nothing_on_a_clean_bill(self):
        urgent = module.audit(module.normalize(URGENT_CARE))
        self.assertEqual(
            [(f["kind"], f["lines"], f.get("of"), f["amount"]) for f in urgent],
            [("duplicate", ["L4"], "L2", Decimal("4.26"))],
        )
        imaging = module.audit(module.normalize(IMAGING))
        self.assertEqual(
            [(f["kind"], f["lines"], f["amount"]) for f in imaging],
            [("balance_billed", ["L1"], Decimal("1730.00"))],
        )
        self.assertEqual(module.audit(module.normalize(PHYSIO)), [])

    def test_audit_only_flags_balance_billing_in_network_and_checks_totals(self):
        out = dict(IMAGING, network="out")
        self.assertEqual(module.audit(module.normalize(out)), [])
        unknown = dict(IMAGING, network="unknown")
        self.assertEqual(module.audit(module.normalize(unknown)), [])
        wrong_total = dict(PHYSIO, total_you_owe=200.00)
        self.assertEqual(
            [(f["kind"], f["amount"]) for f in module.audit(module.normalize(wrong_total))],
            [("total_mismatch", Decimal("22.00"))],
        )

    def test_normalize_bounds_whatever_the_model_returns(self):
        raw = {
            "readable": True,
            "kind": "ransom_note",
            "network": "maybe",
            "provider": "x" * 500,
            "lines": [line("d", "c", "NaN", -5, 1e12, "12.345", "y" * 500)] * 30 + ["bad"],
            "total_you_owe": "lots",
        }
        bill = module.normalize(raw)
        self.assertEqual((bill["kind"], bill["network"]), ("other", "unknown"))
        self.assertEqual(len(bill["lines"]), module.MAX_LINES)
        self.assertEqual(len(bill["provider"]), 60)
        first = bill["lines"][0]
        self.assertEqual(
            (first["billed"], first["allowed"], first["plan_paid"], first["you_owe"]),
            (Decimal(0), Decimal(0), Decimal(0), Decimal("12.34")),
        )
        self.assertEqual(len(first["description"]), 60)
        self.assertEqual(bill["total_you_owe"], Decimal(0))
        self.assertFalse(module.normalize({"readable": True, "lines": []})["readable"])

    def test_prepare_image_rejects_non_images_and_shrinks_and_strips_photos(self):
        with self.assertRaises(UnidentifiedImageError):
            module.prepare_image(b"%PDF-1.7 not an image")
        big = Image.new("RGB", (4000, 3000), "white")
        exif = Image.Exif()
        exif[0x010F] = "PhoneMaker"
        raw = io.BytesIO()
        big.save(raw, format="JPEG", exif=exif)
        out = Image.open(io.BytesIO(module.prepare_image(raw.getvalue())))
        self.assertEqual(out.format, "JPEG")
        self.assertEqual(max(out.size), module.MAX_SIDE)
        self.assertFalse(dict(out.getexif()))
        sample = (SAMPLES / "urgent-care-eob.png").read_bytes()
        self.assertEqual(Image.open(io.BytesIO(module.prepare_image(sample))).size[0], 1200)

    async def test_upload_is_read_audited_billed_and_summarized_without_amounts(self):
        agent = self.agent()
        session = MagicMock()
        response = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=2000, completion_tokens=500),
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(URGENT_CARE)))],
        )
        agent._vision = MagicMock()
        agent._vision.chat.completions.create = AsyncMock(return_value=response)
        agent._vision.close = AsyncMock()
        png = (SAMPLES / "urgent-care-eob.png").read_bytes()
        with (
            patch.object(type(agent), "session", new_callable=PropertyMock, return_value=session),
            patch.object(agent, "update_chat_ctx", new_callable=AsyncMock) as update,
            patch.object(module, "publish_ui_event") as publish,
            patch.object(hosted, "control", new_callable=AsyncMock) as control,
        ):
            await agent._receive(FakeReader([png[:50_000], png[50_000:]]), "visitor")
            await asyncio.sleep(0)
        sent = agent._vision.chat.completions.create.await_args.kwargs
        self.assertFalse(sent["store"])
        self.assertEqual(sent["response_format"]["json_schema"]["strict"], True)
        image = sent["messages"][1]["content"][0]["image_url"]
        self.assertTrue(image["url"].startswith("data:image/jpeg;base64,"))
        events = {call.args[1]: call.args[2] for call in publish.call_args_list}
        read = events["BillRead"]
        self.assertEqual(
            (read["n"], read["provider"], len(read["lines"])), (1, "Brightwater Urgent Care", 5)
        )
        self.assertEqual(
            read["findings"], [{"kind": "duplicate", "lines": ["L4"], "amount": "4.26"}]
        )
        self.assertEqual(read["lines"][3]["you_owe"], "4.26")
        self.assertEqual(events["BillStatus"]["stage"], "ready")
        context = update.await_args.args[0]
        note = context.items[-1]
        self.assertEqual(note.id, "bill-1")
        self.assertIn("duplicate on L4", note.text_content)
        self.assertNotIn("$", note.text_content)
        self.assertNotIn("4.26", note.text_content)
        session.generate_reply.assert_called_once()
        # 2,000 input tokens at $0.40/M plus 500 output at $1.60/M is 1,600 micro-dollars.
        control.assert_any_await("usage", ID, service="openai", microusd=1600)
        self.assertFalse(agent._busy)
        await self.close(agent)

    async def test_uploads_from_the_wrong_sender_type_or_size_never_reach_vision(self):
        agent = self.agent()
        agent._vision = MagicMock()
        agent._vision.chat.completions.create = AsyncMock()
        agent._vision.close = AsyncMock()
        session = MagicMock()
        cases = [
            (FakeReader([b"x"]), "other-agent"),
            (FakeReader([b"x"]), "stranger"),
            (FakeReader([b"x"], mime="application/pdf"), "visitor"),
            (FakeReader([b"x"], size=module.MAX_UPLOAD_BYTES + 1), "visitor"),
        ]
        with (
            patch.object(type(agent), "session", new_callable=PropertyMock, return_value=session),
            patch.object(module, "publish_ui_event"),
        ):
            for reader, sender in cases:
                await agent._receive(reader, sender)
                self.assertTrue(reader.closed)
            # A stream that lies about its size is cut off while it is read.
            liar = FakeReader([b"x" * 3_000_000, b"x" * 3_000_000], size=10)
            await agent._receive(liar, "visitor")
            self.assertTrue(liar.closed)
            agent._uploads = module.MAX_UPLOADS
            last = FakeReader([b"x"])
            await agent._receive(last, "visitor")
            self.assertTrue(last.closed)
        agent._vision.chat.completions.create.assert_not_awaited()
        await self.close(agent)

    async def test_unreadable_image_asks_for_another_photo(self):
        agent = self.agent()
        session = MagicMock()
        with (
            patch.object(type(agent), "session", new_callable=PropertyMock, return_value=session),
            patch.object(module, "publish_ui_event") as publish,
        ):
            await agent._receive(FakeReader([b"not an image"]), "visitor")
        self.assertEqual(publish.call_args.args[2]["stage"], "failed")
        self.assertIn("could not be read", session.generate_reply.call_args.kwargs["instructions"])
        self.assertIsNone(agent._bill)
        await self.close(agent)

    async def test_show_lines_grounds_answers_and_highlights_the_lines(self):
        agent = self.agent()
        context = MagicMock()
        self.assertIn("No bill yet", await agent.show_lines(context, ["L1"]))
        agent._bill = module.normalize(URGENT_CARE)
        agent._findings = module.audit(agent._bill)
        with patch.object(module, "publish_ui_event") as publish:
            answer = await agent.show_lines(context, ["l4", "TOTAL"])
            missing = await agent.show_lines(context, ["L9"])
        self.assertIn("you owe $4.26", answer)
        self.assertIn("It repeats L2", answer)
        self.assertIn("you owe $69.09", answer)
        self.assertIn("$4.26 in question", answer)
        self.assertIn("This bill has L1, L2, L3, L4, L5", missing)
        publish.assert_called_once_with(agent.room, "BillFocus", {"lines": ["L4"], "total": True})
        await self.close(agent)

    async def test_disputes_only_open_on_flagged_lines_once_each(self):
        agent = self.agent()
        context = MagicMock()
        agent._uploads = 1
        agent._bill = module.normalize(IMAGING)
        agent._findings = module.audit(agent._bill)
        with patch.object(module, "publish_ui_event") as publish:
            refused = await agent.open_dispute(context, ["L2"], "contrast")
            opened = await agent.open_dispute(context, ["L1"], "charged above allowed")
            again = await agent.open_dispute(context, ["l1"], "again")
        self.assertIn("nothing to dispute", refused)
        self.assertIn("$1,730.00", opened)
        self.assertIn("already open", again)
        dispute = publish.call_args.args[2]
        self.assertEqual(publish.call_args.args[1], "BillDispute")
        self.assertEqual((dispute["lines"], dispute["amount"]), (["L1"], "1730.00"))
        self.assertRegex(dispute["ref"], r"^KH-D\d{6}$")
        self.assertEqual(dispute["kinds"], ["balance_billed"])
        await self.close(agent)

    async def test_calls_keep_their_own_bills_and_use_sonic_3(self):
        first = self.agent()
        second = self.agent()
        first._bill = module.normalize(PHYSIO)
        first._uploads = 2
        self.assertIsNone(second._bill)
        self.assertEqual(second._uploads, 0)
        self.assertIsNot(first._vision, second._vision)
        self.assertEqual(first.tts._opts.model, "sonic-3")
        self.assertEqual(hosted.HostedBill.llm_budget, 24)
        self.assertGreater(hosted_bill.vision_cost(None), 0)
        await self.close(first, second)


if __name__ == "__main__":
    unittest.main()
