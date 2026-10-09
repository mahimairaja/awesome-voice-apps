# Hotel concierge with a face

A video concierge at a fictional hotel recommends a restaurant nearby and books
a table. The agent is a normal STT, LLM and TTS pipeline. An
[Anam](https://anam.ai) avatar joins the room as a second participant, takes
the agent's audio and publishes lip-synced audio and video.

The agent also measures the avatar and sends it as a `Sync` UI event: how long
the avatar took to join, the playback delay per reply, and how fast the
concierge went quiet when the caller talked over it.

## Credentials

- LiveKit URL, API key and secret
- `OPENAI_API_KEY` for speech, the model and the voice
- `ANAM_API_KEY` and `ANAM_AVATAR_ID` (pick a stock avatar in the Anam gallery
  or make one in Anam Lab). Anam bills per second of avatar session.

## Run

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Join the room from a LiveKit client that shows video, such as the Agents
Playground. The avatar needs a real room, so console mode does not work here.
Restaurants and bookings are made up; nothing is reserved.
