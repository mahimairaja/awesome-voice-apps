"""Offline tests for eval-call tool reporting; never call provider APIs."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import hosted

ID = "598cd768-86d4-42a1-bb44-adc44fba4207"


def event(*calls):
    return SimpleNamespace(
        function_calls=[SimpleNamespace(name=name, arguments=args) for name, args in calls]
    )


class EvalToolReports(unittest.IsolatedAsyncioTestCase):
    async def test_reports_the_whole_list_each_time(self):
        control = AsyncMock(return_value={"recorded": 1})
        with patch.object(hosted, "control", control):
            tools = hosted.EvalTools(ID)
            tools.record(event(("lookup_prescription", json.dumps({"rx": "4417"}))))
            tools.record(event(("request_refill", "not json")))
            await hosted.asyncio.gather(*list(hosted.tasks))
        last = control.await_args_list[-1]
        self.assertEqual(last.args, ("eval_tools", ID))
        self.assertEqual(
            last.kwargs["tools"],
            [
                {"name": "lookup_prescription", "arguments": {"rx": "4417"}},
                {"name": "request_refill", "arguments": {}},
            ],
        )

    async def test_caps_calls_and_argument_size(self):
        with patch.object(hosted, "control", AsyncMock()):
            tools = hosted.EvalTools(ID)
            tools.record(event(*[("t", json.dumps({"x": "y" * 2000}))] * 70))
            await hosted.asyncio.gather(*list(hosted.tasks))
        self.assertEqual(len(tools.calls), hosted.EvalTools.LIMIT)
        self.assertEqual(tools.calls[0]["arguments"], {"truncated": True})

    async def test_a_failed_report_never_raises(self):
        with patch.object(hosted, "control", AsyncMock(side_effect=hosted.httpx.HTTPError("x"))):
            tools = hosted.EvalTools(ID)
            tools.record(event(("t", "{}")))
            await hosted.asyncio.gather(*list(hosted.tasks))


if __name__ == "__main__":
    unittest.main()
