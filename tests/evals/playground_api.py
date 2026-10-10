"""Client for the mahimai.ca eval API.

The site reserves each eval call from its own monthly eval budget, mints the
LiveKit token that dispatches the hosted demo, and stores the scored call.
See docs/playground-evals.md in the website repository.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx


class EvalBusy(Exception):
    """No eval call is free: the budget is spent or the two call slots are taken."""


@dataclass
class EvalCall:
    id: str
    url: str
    token: str
    deadline: int
    seconds: int


class Site:
    def __init__(
        self,
        base: str | None = None,
        secret: str | None = None,
        client: httpx.Client | None = None,
    ):
        self.base = (base or os.environ.get("EVAL_SITE", "https://mahimai.ca")).rstrip("/")
        secret = secret or os.environ.get("PLAYGROUND_EVAL_SECRET", "")
        if not secret:
            raise RuntimeError("Set PLAYGROUND_EVAL_SECRET to run the demo evals.")
        self.client = client or httpx.Client(timeout=30)
        self.headers = {"authorization": f"Bearer {secret}"}

    def _send(self, method: str, path: str, body: dict) -> dict:
        res = self.client.request(method, self.base + path, json=body, headers=self.headers)
        if res.status_code == 429:
            raise EvalBusy(res.json().get("error", "Eval calls are not available."))
        res.raise_for_status()
        return res.json()

    def start(self, demo: str, seconds: int | None = None, *, tries: int = 3, wait: float = 60):
        """Reserve an eval call, waiting for a free slot a few times."""
        body = {"demo": demo, **({"seconds": seconds} if seconds else {})}
        for attempt in range(tries):
            try:
                data = self._send("POST", "/api/playground/eval-session", body)
                return EvalCall(**{k: data[k] for k in EvalCall.__dataclass_fields__})
            except EvalBusy:
                if attempt == tries - 1:
                    raise
                time.sleep(wait)
        raise AssertionError("unreachable")

    def end(self, call_id: str) -> None:
        self._send("DELETE", "/api/playground/eval-session", {"id": call_id})

    def record(self, record: dict) -> dict:
        return self._send("POST", "/api/playground/evals", {"action": "record", **record})
