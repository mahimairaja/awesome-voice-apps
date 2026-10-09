"""Offline tests for the /status heartbeat; never call provider APIs."""

import json
import unittest

import httpx
import status

ENV = {
    "OPENAI_API_KEY": "sk-test",
    "DEEPGRAM_API_KEY": "dg-test",
    "CARTESIA_API_KEY": "ca-test",
    "PLAYGROUND_ORIGIN": "https://mahimai.ca/",
    "PLAYGROUND_WORKER_SECRET": "s" * 32,
}


class Heartbeat(unittest.TestCase):
    def client(self, statuses, seen):
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.host == "mahimai.ca":
                return httpx.Response(200, json={"recorded": True})
            return httpx.Response(statuses[request.url.host])

        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_beat_reports_demos_and_reads_only(self):
        seen = []
        statuses = {"api.openai.com": 200, "api.deepgram.com": 403, "api.cartesia.ai": 401}
        status.beat(self.client(statuses, seen), {"water", "coffee"}, ENV)
        probes, post = seen[:3], seen[3]
        self.assertTrue(all(r.method == "GET" for r in probes))
        self.assertEqual(post.url, "https://mahimai.ca/api/playground/worker")
        self.assertEqual(post.headers["authorization"], f"Bearer {'s' * 32}")
        body = json.loads(post.content)
        self.assertEqual(body["action"], "heartbeat")
        self.assertEqual(body["demos"], ["coffee", "water"])
        self.assertTrue(body["probes"]["openai"]["ok"])
        # A usage-only Deepgram key answers 403 and is still a working key.
        self.assertTrue(body["probes"]["deepgram"]["ok"])
        self.assertEqual(
            body["probes"]["cartesia"], {**body["probes"]["cartesia"], "ok": False, "status": 401}
        )

    def test_missing_key_is_a_failed_probe(self):
        result = status.probe(self.client({}, []), "openai", {})
        self.assertEqual(result, {"ok": False, "ms": 0, "status": None})

    def test_network_error_is_a_failed_probe(self):
        def boom(request):
            raise httpx.ConnectError("down")

        client = httpx.Client(transport=httpx.MockTransport(boom))
        self.assertFalse(status.probe(client, "cartesia", ENV)["ok"])


if __name__ == "__main__":
    unittest.main()
