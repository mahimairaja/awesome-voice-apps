"""Maintainer-owned adapter for the standalone front desk interpreter demo.

The contributed demo bridges two people on their own devices with Gemini Live.
A playground call is one visitor and the agent, so the visitor plays both the
desk clerk (English) and the guest (any other language), and hears each line
interpreted into the other. The hosted call runs on the playground's metered
speech-to-speech model, GPT-Live, instead of Gemini Live.
"""

import importlib.util
import sys
import types
from pathlib import Path

import voicegateway

# The demo imports the Gemini SDK and the LiveKit Google plugin at module level.
# Neither ships in the hosted image, so give those imports inert placeholders
# for the load only. `types` must exist on google.genai for `from ... import`.
_STUBS = ("google.genai", "livekit.plugins.google")


def _load():
    demo = Path(__file__).resolve().parents[1] / "demos" / "front-desk-interpreter"
    spec = importlib.util.spec_from_file_location("playground_interp_agent", demo / "agent.py")
    module = importlib.util.module_from_spec(spec)
    added = []
    for key in _STUBS:
        if key not in sys.modules:
            stub = types.ModuleType(key)
            stub.types = types.ModuleType(f"{key}.types")
            sys.modules[key] = stub
            added.append(key)
    try:
        spec.loader.exec_module(module)
    finally:
        for key in added:
            sys.modules.pop(key, None)
    return module


_module = _load()
FrontDeskInterpreter = _module.FrontDeskInterpreter

DESK_LANGUAGE = "English"
# Interpretation needs no tools; the backend should never be asked for work.
BACKEND_INSTRUCTIONS = "You have no tools. Return an empty result if asked for anything."
HOSTED_INSTRUCTIONS = (
    " In this public demo one visitor plays both people on the same device: the "
    "desk clerk and the guest. Interpret every line the same way regardless."
)
GREETING = (
    f"In two short {DESK_LANGUAGE} sentences: say this is a front desk interpreter "
    "simulation, and that they can play the guest in any language and the desk "
    f"clerk in {DESK_LANGUAGE}, and you will interpret each line."
)
# One response per interpreted line; a full two-minute call needs far fewer.
MAX_RESPONSES = 30


class HostedInterpreter(FrontDeskInterpreter):
    def __init__(self, room, approval, finish, spawn, sink):
        super().__init__(DESK_LANGUAGE)
        self._instructions = _module.build_instructions(DESK_LANGUAGE) + HOSTED_INSTRUCTIONS
        self.room = room
        self.approval = approval
        self.finish = finish
        self.spawn = spawn
        self.sink = sink
        self.responses = 0
        self.captions: list[dict] = []
        self.pending: str | None = None
        self.greeted = False

    def hear(self, text: str) -> None:
        # Finals are transcription segments, not turns: keep them until the
        # interpretation that answers them arrives, like the contributed demo.
        text = (text or "").strip()
        if text:
            self.pending = f"{self.pending} {text}" if self.pending else text

    def interpreted(self, item) -> None:
        # The greeting is not an interpretation; keep it off the captions.
        if not self.greeted or getattr(item, "role", None) != "assistant":
            return
        text = (getattr(item, "text_content", None) or "").strip()
        if not text:
            return
        row = {"text": text[:400]}
        if self.pending:
            row["original"] = self.pending[:400]
            self.pending = None
        self.captions.append(row)
        del self.captions[: -_module.MAX_CAPTION_ROWS]
        _module._publish_captions(self.room, self.captions)

    def budget_event(self, event):
        if (
            event.get("type") == "response.event"
            and event.get("event", {}).get("type") == "response.created"
        ):
            self.responses += 1
            if self.responses > MAX_RESPONSES:
                self.spawn(self.finish())

    async def on_enter(self):
        voicegateway.attach(
            self.session,
            project="mahimai-playground",
            agent_id="interp",
            sink=self.sink,
            room=self.room.name,
            transcript=False,
            snapshots=False,
            turns=False,
            dead_air=False,
        )
        self.duplex_session.on("openai_server_event_received", self.budget_event)
        self.session.on("conversation_item_added", lambda event: self.interpreted(event.item))
        _module._publish_captions(self.room, self.captions)
        handle = self.session.generate_reply(instructions=GREETING)
        await handle
        self.greeted = True
        self.pending = None
