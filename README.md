# Awesome Voice Apps

Voice AI agents that don't stop at hello.

> **Customer:** I'd like a 12oz iced latte, oat milk, light ice.  
> **Agent:** Got it. Anything else, or should I total it for you?  
> **Customer:** Add a chocolate croissant.  
> **Agent:** One iced latte, light ice, oat milk. One chocolate croissant. That's $9.40. What name do I put on it?

Each folder under [`demos/`](demos/) is a self-contained voice agent
that you clone, fill with your own credentials, and have a real
conversation with. Built on [LiveKit Agents](https://docs.livekit.io)
1.x with Deepgram STT, OpenAI LLMs, and Cartesia TTS by default. Any
provider swappable in a config change. All Apache-2.0.

## Run the template

```sh
git clone https://github.com/mahimairaja/awesome-voice-apps.git
cd awesome-voice-apps/templates/livekit-base
cp .env.example .env  # six keys: LiveKit, OpenAI, Deepgram, Cartesia
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Open [agents-playground.livekit.io](https://agents-playground.livekit.io)
in a browser, connect, and talk to the agent. The starter at
[`templates/livekit-base/`](templates/livekit-base/) is what every demo
copies from. Most demos change the `Assistant` instructions, add a
`@function_tool`, optionally swap a provider, and ship.

## Categories

| Category | What kind of voice agent fits here |
| --- | --- |
| **Receptionist & Booking** | Picks up an inbound call, qualifies the caller, schedules an appointment. |
| **Drive-thru & Ordering** | Takes an order, modifies items mid-flow, runs totals, hands off cleanly. |
| **Customer Support** | Status, returns, account changes, warm handoff to a human. |
| **Companion & Coaching** | Practice partners, journaling assistants, accountability coaches, language tutors. |
| **Education** | Flashcards, Socratic Q&A, reading helpers, quiz drills. |
| **Data Extraction** | Captures structured data through conversation: intake forms, surveys, KYC. |
| **Tool Calling** | Calls APIs, MCP servers, web search mid conversation. |
| **Multilingual** | Switches language, handles code switching, translates within a single conversation. |
| **Multi-Agent** | Specialized personas hand off to each other: triage to specialist, sales to support. |
| **Telephony & SIP** | Real phone calls. Inbound IVR, outbound surveys, warm transfer. |

When demos exist, each category links to its demos here, with a bold
name and a one-line description per demo.

## What you get with each demo

Every demo folder ships with the same six files: `agent.py`,
`pyproject.toml`, `.env.example`, a demo-specific `README.md`, a written
walkthrough (`blog.md`), and a short reel script (`reel.md`). Run the
demo on its own, or open the folder and read the walkthrough top to
bottom.

## Adding a demo

See [CONTRIBUTING.md](CONTRIBUTING.md) for the folder layout, naming
convention, and the build budget.

## License

[Apache 2.0](LICENSE). Fork it, ship it, sell it.

## Built by

[Mahimai Raja](https://mahimai.com), founder of
[Mahimai AI](https://mahimai.com), a voice agent agency.
