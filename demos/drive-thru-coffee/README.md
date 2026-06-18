# drive-thru coffee

Voice agent that takes a coffee order, modifies items mid-flow, runs a
live total in the playground UI, and closes with a pickup name.

Category: restaurant.

## What it does

- Reads a ten-item menu (espressos, lattes, cold brew, pastries).
- Adds items, removes items, accepts modifications such as oat milk,
  light ice, decaf, or an extra shot.
- Mounts an Order, a Total, and a Checkout card on the playground
  screen and updates them as the call goes.
- Confirms with a name and reads back the total.

The agent uses three function tools: `add_item`, `remove_item`,
`submit_order`. Cart and pricing live in the agent process. The
playground only renders what the tools publish.

## Try it

You need a free [LiveKit Cloud](https://cloud.livekit.io/) project (or
a self-hosted LiveKit server), plus API keys for OpenAI, Deepgram, and
Cartesia.

```sh
git clone https://github.com/mahimairaja/awesome-voice-apps.git
cd awesome-voice-apps/demos/drive-thru-coffee
cp .env.example .env
uv sync
uv run --no-project python agent.py dev
```

Then open
[playground.mahimai.ca/demos/drive-thru-coffee](https://playground.mahimai.ca/demos/drive-thru-coffee)
and connect. Order a latte, add oat milk, throw in a croissant, give a
name, and watch the cards on the right.

## Recording

Coming after the demo is recorded.

## Why this demo exists

It is the first cookbook demo. Its job is to exercise the scaffold:
three function tools, playground UI events, room metadata. Every other
demo copies this shape.
