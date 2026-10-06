"""Maintainer-owned adapter for the standalone SDR demo and hosted call limits."""

import importlib.util
import json
import sys
from pathlib import Path

import voicegateway

# Keep the contributed demo self-contained; do not duplicate its booking logic.
_demo = Path(__file__).resolve().parents[1] / "talk-to-our-team"
sys.path.append(str(_demo))
_spec = importlib.util.spec_from_file_location("playground_sdr_agent", _demo / "agent.py")
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
SalesAssistant = _module.SalesAssistant
BACKEND_INSTRUCTIONS = _module.BACKEND_INSTRUCTIONS


def meeting_state(booking):
    """Only publish calendar state, never caller transcripts or project details."""
    return {
        "day": booking.day if booking.day in {"monday", "friday"} else "",
        "slot": booking.booked["slot"] if booking.booked else booking.proposed,
        "booked": booking.booked is not None,
    }


class HostedSDR(SalesAssistant):
    def __init__(self, room, approval, finish, spawn, sink):
        super().__init__()
        self.room = room
        self.approval = approval
        self.finish = finish
        self.spawn = spawn
        self.sink = sink
        self.responses = 0

    async def publish(self):
        await self.room.local_participant.publish_data(
            json.dumps(
                {"type": "ui_event", "component": "Meeting", "props": meeting_state(self.booking)}
            ).encode(),
            reliable=True,
            topic="ui",
        )

    def hear(self, text):
        super().hear(text)
        self.spawn(self.publish())

    def budget_event(self, event):
        if (
            event.get("type") == "response.event"
            and event.get("event", {}).get("type") == "response.created"
        ):
            self.responses += 1
            if self.responses > 16:
                self.spawn(self.finish())

    async def on_enter(self):
        voicegateway.attach(
            self.session,
            project="mahimai-playground",
            agent_id="sdr",
            sink=self.sink,
            room=self.room.name,
            transcript=False,
            snapshots=False,
            turns=False,
            dead_air=False,
        )
        self.duplex_session.on("openai_server_event_received", self.budget_event)
        self.session.on("function_tools_executed", lambda _: self.spawn(self.publish()))
        await self.publish()
        await super().on_enter()
