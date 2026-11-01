"""Eval copy of a demo, served by LiveKit Inference on LiveKit Cloud.

The scripted caller in tests/evals talks to this agent instead of the hosted
playground worker. It runs the demo's own Agent class (instructions, tools,
UI events) and swaps only the speech and model clients for LiveKit Inference,
so it needs no provider keys: LiveKit Cloud supplies its own credentials.

deploy.sh copies the demo's agent.py next to this file as demo.py.
"""

from __future__ import annotations

import logging
import os

import demo
from livekit.agents import AgentServer, AgentSession, JobContext, JobProcess, cli, inference

logger = logging.getLogger("eval-agent")

# The demo's stack, through LiveKit Inference. Override per deploy if needed.
STT = os.environ.get("EVAL_STT", "deepgram/nova-3")
LLM = os.environ.get("EVAL_LLM", "openai/gpt-4o-mini")
TTS = os.environ.get("EVAL_TTS", "cartesia/sonic-3:9626c31c-bec5-4cca-baa8-f8ba9e84c8bc")
AGENT_NAME = os.environ.get("EVAL_AGENT_NAME", "eval-coffee")

server = AgentServer()


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = inference.VAD()


server.setup_fnc = prewarm


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    userdata = {"cart": []}
    session = AgentSession(
        userdata=userdata,
        stt=inference.STT(STT, language="en"),
        llm=inference.LLM(LLM),
        tts=inference.TTS(TTS.split(":")[0], voice=TTS.partition(":")[2] or None),
        vad=ctx.proc.userdata["vad"],
        turn_detection=inference.TurnDetector(),
    )
    session.on("error", lambda event: logger.error("session error: %s", event.error))
    await session.start(agent=demo.DriveThruAttendant(ctx.room), room=ctx.room)
    await ctx.connect()
    demo._publish_cart(ctx.room, userdata["cart"])
    demo._publish_menu(ctx.room)
    await session.generate_reply(instructions="Greet the customer and ask what they would like.")


if __name__ == "__main__":
    cli.run_app(server)
