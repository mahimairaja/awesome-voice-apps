# Hotel concierge with a face

A concierge with a face at a fictional hotel recommends a restaurant nearby
and books a table. The agent is a normal STT, LLM and TTS pipeline. A
[Spatius](https://spatius.ai) avatar joins the room as a second participant,
takes the agent's audio and publishes it with motion data. The browser renders
the face with the Spatius web SDK (3D Gaussian splatting), so lips and voice
leave the avatar participant together.

The agent also measures the avatar and sends it as a `Sync` UI event: how long
the avatar took to join, the playback delay per reply, and how fast the
concierge went quiet when the caller talked over it.

## Credentials

- LiveKit URL, API key and secret
- `OPENAI_API_KEY` for speech, the model and the voice
- `SPATIUS_APP_ID` and `SPATIUS_API_KEY` from Spatius Studio, and
  `SPATIUS_AVATAR_ID` from its Avatar Library. Spatius bills avatar session
  time in credits.

## Run

The avatar plugin encodes audio with libopus: `brew install opus` on macOS,
`apt install libopus0` on Debian or Ubuntu.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Join the room from a frontend that uses the Spatius web SDK
(`@spatius/avatarkit` and `@spatius/avatarkit-rtc`); see the
[client guide](https://docs.spatius.ai/livekit-agents/client). The face is
drawn in the browser, so console mode and the Agents Playground show a black
frame.
Restaurants and bookings are made up; nothing is reserved.
