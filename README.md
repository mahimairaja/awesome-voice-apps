<div align="center">

# awesome-voice-apps

Voice agents that answer the phone, take the order, book the slot.
A cookbook of small, self-contained voice AI you can clone and talk to.

[playground](https://playground.mahimai.ca) · [contribute](CONTRIBUTING.md) · [license](LICENSE)

</div>

> **You:** A 12oz iced latte, oat milk, light ice.
> **Agent:** Got it. Anything else, or should I total it?
> **You:** Add a chocolate croissant.
> **Agent:** One iced latte, light ice, oat milk; one chocolate croissant. That is $9.40. What name should I put on it?

Each folder under [`demos/`](demos/) is one voice agent: a few hundred lines of Python on [LiveKit Agents](https://docs.livekit.io/agents), Deepgram for hearing, an LLM for thinking, Cartesia for speaking. Swap any provider in a line. Run one locally, then talk to it in your browser at [playground.mahimai.ca](https://playground.mahimai.ca).

## Run one

```sh
git clone https://github.com/mahimairaja/awesome-voice-apps.git
cd awesome-voice-apps/templates/livekit-base
cp .env.example .env
uv sync
uv run --no-project python agent.py download-files
uv run --no-project python agent.py dev
```

Open the demo at [playground.mahimai.ca/demos](https://playground.mahimai.ca/demos), paste your three LiveKit values, and start the call. [`templates/livekit-base/`](templates/livekit-base/) is the starter every demo copies from: change the instructions, add a `@function_tool`, ship.

## The demos

| Demo | Industry | What it does |
| --- | --- | --- |
| [clinic-scheduler](demos/clinic-scheduler/) | healthcare | Books a doctor appointment by voice, finds open slots, and handles reschedules. |
| [drive-thru-coffee](demos/drive-thru-coffee/) | restaurant | Takes a coffee order, edits it mid-flow, totals the cart. |
| [quick-trivia](demos/quick-trivia/) | education | Quizzes the caller and keeps score on screen. |
| [order-returns](demos/order-returns/) | retail | Starts a return by voice: look up an order, pick items with reasons, get a refund summary. |
| [water-tracker](demos/water-tracker/) | fitness | Logs glasses of water against a daily goal. |

Every demo is tagged with the one industry it serves: healthcare, legal, finance, realestate, hospitality, restaurant, automotive, education, retail, recruiting, construction, travel, fitness, beauty, logistics, insurance, nonprofit, gov, media. Per-demo metadata lives in [`catalog.json`](catalog.json).

## Add one

A demo is a folder under `demos/<slug>/`: an `agent.py`, a `pyproject.toml`, a short README, and a `playground.json` when it draws UI. Keep it under 300 lines on top of the template. The day-to-day flow is a single GitHub issue (slug, hook, stack), and a build agent scaffolds the rest. Details in [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache 2.0](LICENSE). Fork it, ship it, sell it.

Built by [Mahimai Raja](https://mahimai.dev) at [Mahimai AI](https://mahimai.ca).
