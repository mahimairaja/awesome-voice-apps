# Awesome Voice Apps

A public cookbook of voice AI demos, shipped one per day. Modeled on
[awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps)
and the Unwind AI tutorial cadence, tuned for voice.

Every demo is a self-contained folder under `demos/`. Copy, fill in your
own credentials, run, and have a real spoken conversation with the agent
in under ten seconds.

## Why this exists

**Short term.** Drive awareness for [Mahimai AI](https://mahimai.com), a
small voice agent agency.

**Long term.** Seed [ShipVoice](https://shipvoice.dev), a paid voice AI
starter kit for indie hackers.

Each demo doubles as a working artifact (the code), a tutorial (the
blog), a reel (the short form video), and a sales touchpoint.

## Quick start

You need three things:

1. A free [LiveKit Cloud](https://cloud.livekit.io) project (or a
   self-hosted LiveKit server).
2. API keys for OpenAI, Deepgram, and Cartesia.
3. [uv](https://docs.astral.sh/uv/) installed locally.

Then:

```sh
git clone https://github.com/mahimairaja/awesome-voice-apps
cd awesome-voice-apps/templates/livekit-base
cp .env.example .env  # fill in your six keys
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Open [agents-playground.livekit.io](https://agents-playground.livekit.io)
in a browser, connect, and talk to the agent.

To run a specific demo, swap `templates/livekit-base` for the demo
folder under `demos/` (each demo has its own README with the same four
steps).

## Categories

Browse demos by category. Every demo is also listed chronologically in
[INDEX.md](INDEX.md).

<details>
<summary><b>Receptionist & Booking</b></summary>

Voice agents that take inbound calls, qualify the caller, and schedule
or capture an appointment.

_No demos yet. First one lands during M0.5._

</details>

<details>
<summary><b>Drive-thru & Ordering</b></summary>

Voice agents that take an order, modify items mid-flow, run totals, and
hand off to a human or POS.

_No demos yet._

</details>

<details>
<summary><b>Customer Support</b></summary>

Voice agents that handle status questions, returns, account changes,
and warm-handoff to a human when the conversation hits its limits.

_No demos yet._

</details>

<details>
<summary><b>Companion & Coaching</b></summary>

Voice agents that hold longer, more open conversations: practice
partners, journaling assistants, accountability coaches, language tutors.

_No demos yet._

</details>

<details>
<summary><b>Education</b></summary>

Voice agents that teach: flashcards, Socratic Q&A, reading helpers,
quiz drills.

_No demos yet._

</details>

<details>
<summary><b>Data Extraction</b></summary>

Voice agents that capture structured data through conversation: intake
forms, surveys, KYC, application questionnaires.

_No demos yet._

</details>

<details>
<summary><b>Tool Calling</b></summary>

Voice agents that call out to tools, APIs, or MCP servers mid
conversation: lookups, bookings, payments, web search.

_No demos yet._

</details>

<details>
<summary><b>Multilingual</b></summary>

Voice agents that switch languages, translate, or handle code switching
within a single conversation.

_No demos yet._

</details>

<details>
<summary><b>Multi-Agent</b></summary>

Voice agents that hand off between specialised personas: triage to
specialist, sales to support, narrator to NPC.

_No demos yet._

</details>

<details>
<summary><b>Telephony & SIP</b></summary>

Voice agents that take or place real phone calls via SIP, including
inbound IVR, outbound surveys, and warm transfer.

_No demos yet._

</details>

## Templates

There is one template, on purpose:
[`templates/livekit-base/`](templates/livekit-base/). Every demo copies
from it. Most demos change the agent instructions, add a `@function_tool`
or two, and ship.

If a demo would need to materially extend the template, it becomes a
template extension rather than a daily demo.

## Run a demo elsewhere

Each demo's `README.md` has the run instructions. The default path is
the same four steps shown above, against the
[agents-playground.livekit.io](https://agents-playground.livekit.io)
frontend. You can also clone
[livekit-examples/agent-starter-react](https://github.com/livekit-examples/agent-starter-react)
locally if you want a frontend of your own.

## Contributing

This repo is the operator's daily ship cookbook, not yet a community
contribution magnet. If you want to add a demo or fix a bug, see
[CONTRIBUTING.md](CONTRIBUTING.md) for the conventions.

## License

[Apache 2.0](LICENSE). Fork, ship, sell. The license matches
[awesome-llm-apps](https://github.com/Shubhamsaboo/awesome-llm-apps).
