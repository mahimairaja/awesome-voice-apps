"""Heartbeat for the mahimai.ca /status page.

Once a minute the worker tells the site which demos it has loaded and whether
each provider key still answers. Every probe is a free read (list a model, a
project, a voice), never inference, so the heartbeat costs nothing to run.
"""

import logging
import os
import threading
import time

import httpx

logger = logging.getLogger(__name__)
INTERVAL_SECONDS = 60
VERSION = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:12] or None

# (provider, url, headers from the environment). Only reads, never billed calls.
PROBES = {
    "openai": lambda env: (
        "https://api.openai.com/v1/models/gpt-4o-mini",
        {"Authorization": f"Bearer {env['OPENAI_API_KEY']}"},
    ),
    "deepgram": lambda env: (
        "https://api.deepgram.com/v1/projects",
        {"Authorization": f"Token {env['DEEPGRAM_API_KEY']}"},
    ),
    "cartesia": lambda env: (
        "https://api.cartesia.ai/voices?limit=1",
        {"X-API-Key": env["CARTESIA_API_KEY"], "Cartesia-Version": "2025-04-16"},
    ),
}


def probe(client: httpx.Client, provider: str, env=os.environ) -> dict:
    started = time.monotonic()
    try:
        url, headers = PROBES[provider](env)
        status = client.get(url, headers=headers).status_code
    except KeyError:
        return {"ok": False, "ms": 0, "status": None}
    except httpx.HTTPError:
        status = None
    ms = min(60_000, round((time.monotonic() - started) * 1000))
    # A 403 from Deepgram means the key is valid but scoped to usage only.
    ok = status is not None and (status < 400 or (provider == "deepgram" and status == 403))
    return {"ok": ok, "ms": ms, "status": status}


def beat(client: httpx.Client, demos, env=os.environ) -> None:
    origin = env["PLAYGROUND_ORIGIN"].rstrip("/")
    body = {
        "action": "heartbeat",
        "demos": sorted(demos),
        "probes": {name: probe(client, name, env) for name in PROBES},
    }
    if VERSION:
        body["version"] = VERSION
    client.post(
        f"{origin}/api/playground/worker",
        headers={"Authorization": f"Bearer {env['PLAYGROUND_WORKER_SECRET']}"},
        json=body,
    ).raise_for_status()


def start(demos) -> threading.Thread:
    """Beat forever on a daemon thread; a failed beat only logs and waits."""

    def run():
        with httpx.Client(timeout=5) as client:
            while True:
                try:
                    beat(client, demos)
                except Exception:
                    logger.warning("status heartbeat failed", exc_info=True)
                time.sleep(INTERVAL_SECONDS)

    thread = threading.Thread(target=run, name="status-heartbeat", daemon=True)
    thread.start()
    return thread
