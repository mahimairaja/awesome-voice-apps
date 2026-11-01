"""Calls to an eval agent on LiveKit Cloud, without the mahimai.ca site.

Used when EVAL_TARGET=cloud. The agent is the demo's own Agent class deployed
with tests/evals/cloud_agent (LiveKit Inference, no provider keys). Each call
gets a fresh room, and a token that dispatches the agent named
``eval-<demo>`` (or EVAL_AGENT_NAME). Records are written to a local JSON
file instead of the site, so judge_claude.py --dry-run can score them.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from datetime import timedelta
from pathlib import Path

from livekit import api
from playground_api import EvalCall

CALL_SECONDS = 120


class CloudSite:
    """Same calls as playground_api.Site, against LiveKit Cloud directly."""

    def __init__(self, out: str | None = None):
        self.url = os.environ.get("LIVEKIT_URL", "")
        self.key = os.environ.get("LIVEKIT_API_KEY", "")
        self.secret = os.environ.get("LIVEKIT_API_SECRET", "")
        if not (self.url and self.key and self.secret):
            raise RuntimeError(
                "EVAL_TARGET=cloud needs LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET."
            )
        self.out = Path(out or os.environ.get("EVAL_RECORDS") or "calls.json")

    def agent_name(self, demo: str) -> str:
        return os.environ.get("EVAL_AGENT_NAME") or f"eval-{demo}"

    def start(self, demo: str, seconds: int | None = None) -> EvalCall:
        call_id = f"eval-{demo}-{uuid.uuid4().hex[:10]}"
        seconds = seconds or CALL_SECONDS
        token = (
            api.AccessToken(self.key, self.secret)
            .with_identity(f"caller-{call_id}")
            .with_grants(api.VideoGrants(room_join=True, room=call_id, can_publish_data=True))
            .with_room_config(
                api.RoomConfiguration(
                    agents=[api.RoomAgentDispatch(agent_name=self.agent_name(demo))]
                )
            )
            .with_ttl(timedelta(seconds=seconds + 60))
            .to_jwt()
        )
        now = int(time.time())
        return EvalCall(
            id=call_id, url=self.url, token=token, deadline=now + seconds, seconds=seconds
        )

    def end(self, call_id: str) -> None:
        """Close the room so the agent leaves at once; a missing room is fine."""

        async def close() -> None:
            async with api.LiveKitAPI(self.url, self.key, self.secret) as lk:
                try:
                    await lk.room.delete_room(api.DeleteRoomRequest(room=call_id))
                except api.TwirpError:
                    pass

        asyncio.run(close())

    def record(self, record: dict) -> dict:
        calls = json.loads(self.out.read_text())["calls"] if self.out.exists() else []
        # Same shape the site hands the judge: the turns are the transcript.
        calls.append({**record, "transcript": record.get("turns", []), "tools": []})
        self.out.write_text(json.dumps({"calls": calls}, indent=1, default=str))
        return {"ok": True}
