# water-tracker

Logs glasses of water by voice and tracks progress toward a daily goal.

Tell the agent how many glasses you have drunk and it updates the live stat card instantly. Change your daily goal mid-session by voice.

## Run it

```sh
cp .env.example .env
# fill the six keys: LiveKit, OpenAI, Deepgram, Cartesia
uv sync
uv run --no-project python agent.py dev
```

Open [playground.mahimai.ca/demos/water-tracker](https://playground.mahimai.ca/demos/water-tracker) and talk to the agent.

## Recording

Coming soon.
