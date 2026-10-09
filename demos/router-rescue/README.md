# Router rescue

Phone support for a fictional internet provider that can see your router. Point
a camera at the front lights and the agent reads them, works out what is wrong
from a fixed playbook, and walks you through the fix one step at a time.

What makes it work:

- **Looks on demand.** The agent has a `look_at_camera` tool and only calls it
  when the answer depends on what the router shows. The camera stream is opened
  for that look and closed again, so the model sees a handful of frames per call,
  not thousands.
- **Three frames, one image.** A single frame cannot tell a blinking light from
  a solid one. Each look grabs three frames 0.35 s apart and sends them as one
  side-by-side strip, so the vision model sees the blink for the price of one image.
- **The playbook decides.** The vision model only reports light states as
  structured JSON. `diagnose()` maps them to a fix, so the advice never drifts.
- **Repeat looks are reused.** A second look at the same thing within three
  seconds returns the first reading, and a call is capped at six looks.

## Credentials

LiveKit, OpenAI (voice LLM and `gpt-4.1-mini` vision), Deepgram and Cartesia.
See `.env.example`.

## Run

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Join the room from a LiveKit client that publishes a camera and a microphone,
for example the [Agents Playground](https://agents-playground.livekit.io). No
router to hand? Show it a photo of one on another screen.

Optional UI events (`RouterLook`, `RouterStats` on topic `ui`, and a JPEG of
each look on byte stream topic `router-frame`) need a compatible client; the
call works without them. Frames are never written to disk. Provider usage may
cost money.
