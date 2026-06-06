# order returns

Voice agent that starts a product return: look up an order, add items to the return cart with a reason, and get a refund summary on screen.

Category: retail.

## What it does

- Looks up a sample order by ID (try ORD-001, ORD-002, ORD-003).
- Adds items to a return cart with a reason: changed mind, wrong size, defective or damaged, not as described, arrived too late.
- Removes items if the caller changes their mind.
- Submits the return and mounts a refund Card on the playground screen.

Four function tools: `look_up_order`, `add_return_item`, `remove_return_item`, `submit_return`. Order data and return cart live in the agent process. All voice powered by OpenAI: Whisper for speech, gpt-4o-mini for reasoning, TTS for replies.

## Try it

You need a free [LiveKit Cloud](https://cloud.livekit.io/) project and an OpenAI API key.

```sh
git clone https://github.com/mahimairaja/awesome-voice-apps.git
cd awesome-voice-apps/demos/order-returns
cp ../../templates/livekit-base/.env.example .env
uv sync
uv run --no-project python agent.py download-files
uv run --no-project python agent.py dev
```

Then open [playground.mahimai.ca/demos/order-returns](https://playground.mahimai.ca/demos/order-returns) and connect. Give an order ID, say which items to return and why, then ask the agent to submit the return.

## Recording

Coming after the demo is recorded.
