"""Client for the mahimai.ca eval API.

The site reserves each eval call from its own monthly eval budget, mints the
LiveKit token that dispatches the hosted demo, and stores the scored call.
See docs/playground-evals.md in the website repository.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx


# The eval secret goes only to the site or one of its Cloudflare previews.
SITES = re.compile(r"https://(mahimai\.ca|[a-z0-9-]+-mahimai\.mahimairaja3\.workers\.dev)")

# How long the site lets an eval call last. It rejects a request outside
# MIN_EVAL_SECONDS to callSeconds(demo) with a 400. Copied from the website
# repository: src/server/playground/evals.ts (MIN_EVAL_SECONDS, evalSeconds)
# and src/config/playground.ts (callSeconds: a demo's callSeconds, else 120).
MIN_EVAL_SECONDS = 30
DEFAULT_CALL_CAP = 120
CALL_CAPS = {"resume": 75}


def call_cap(demo: str) -> int:
    """The longest eval call the site accepts for ``demo``, in seconds."""
    return CALL_CAPS.get(demo, DEFAULT_CALL_CAP)


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
        if base is None:
            base = (os.environ.get("EVAL_SITE") or "https://mahimai.ca").rstrip("/")
            if not SITES.fullmatch(base):
                raise RuntimeError(
                    f"EVAL_SITE must be mahimai.ca or one of its previews, not {base}."
                )
        self.base = base.rstrip("/")
        # EVAL_MODELS='{"stt": "...", "llm": "...", "tts": "..."}' runs switchable demos
        # on those models instead of the demo's defaults.
        self.models = json.loads(os.environ.get("EVAL_MODELS") or "null")
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
        body = {
            "demo": demo,
            **({"seconds": seconds} if seconds else {}),
            **({"models": self.models} if self.models else {}),
        }
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
        """Post a scored call, or with EVAL_RECORDS keep it in that file instead.

        A kept call never reaches the public evals page; the judge reads the file with
        ``judge_claude.py --dry-run``. The worker still stores the call's tool calls
        under its session id, so they can be joined in later.
        """
        out = os.environ.get("EVAL_RECORDS")
        if not out:
            return self._send("POST", "/api/playground/evals", {"action": "record", **record})
        path = Path(out)
        calls = json.loads(path.read_text())["calls"] if path.exists() else []
        calls.append({**record, "transcript": record.get("turns", []), "tools": []})
        path.write_text(json.dumps({"calls": calls}, indent=1, default=str))
        return {"ok": True}
