<div align="center">

# 🎙️ Awesome Voice Apps

**Voice AI agents that don't stop at hello.**

Receptionists. Drive-thrus. Multilingual coaches. Intake forms. Each one self-contained, under 300 lines, Apache 2.0.

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg?style=for-the-badge)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org)
[![LiveKit Agents 1.x](https://img.shields.io/badge/livekit--agents-1.x-FF5C29?style=for-the-badge)](https://docs.livekit.io/agents)
[![GitHub stars](https://img.shields.io/github/stars/mahimairaja/awesome-voice-apps?style=for-the-badge&color=FFD700)](https://github.com/mahimairaja/awesome-voice-apps/stargazers)
[![Last commit](https://img.shields.io/github/last-commit/mahimairaja/awesome-voice-apps?style=for-the-badge&color=8A2BE2)](https://github.com/mahimairaja/awesome-voice-apps/commits/main)

[**🚀 Run the template**](#-run-the-template) · [**🚧 Up next**](#-up-next) · [**📚 Categories**](#-categories-10) · [**🤝 Add a demo**](CONTRIBUTING.md)

</div>

> **Customer:** I'd like a 12oz iced latte, oat milk, light ice.
> **Agent:** Got it. Anything else, or should I total it for you?
> **Customer:** Add a chocolate croissant.
> **Agent:** One iced latte, light ice, oat milk. One chocolate croissant. That's $9.40. What name do I put on it?

Each folder under [`demos/`](demos/) is a self-contained voice agent
that you clone, fill with your own credentials, and have a real
conversation with. Built on [LiveKit Agents](https://docs.livekit.io)
1.x with Deepgram STT, OpenAI LLMs, and Cartesia TTS by default. Any
provider swappable in a config change. All Apache 2.0.

## 🚀 Run the template

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

## 📂 Repo layout

```
awesome-voice-apps/
├── README.md                this file, with the demo catalog by category
├── CONTRIBUTING.md          folder layout, naming, build budget
├── CLAUDE.md                operating instructions for any Claude session in this repo
├── templates/
│   └── livekit-base/        the starter every demo copies from
├── demos/
│   └── <slug>/              one folder per demo, six files
└── LICENSE
```

## 🚧 Up next

The first demo lands in **Drive-thru & Ordering**: a coffee-order agent
that takes orders, modifies items mid-flow, totals the cart, and ends
the call cleanly. When it ships, the row below populates and every
category turns into a clickable section.

| Slug | What it does | Category | Status |
| --- | --- | --- | :---: |
| `drive-thru-coffee` | Takes a coffee order, modifies items, runs totals, hands off | Drive-thru & Ordering | 🟡 In flight |

## 📚 Categories (10)

Ten lanes for voice agents. Click a row to see the demos in it when
they exist; right now the table is the index, the demos come next.

| Category | What kind of voice agent fits here |
| --- | --- |
| 🛎️ **Receptionist & Booking** | Picks up an inbound call, qualifies the caller, schedules an appointment. |
| 🥤 **Drive-thru & Ordering** | Takes an order, modifies items mid-flow, runs totals, hands off cleanly. |
| 💬 **Customer Support** | Status, returns, account changes, warm handoff to a human. |
| 🧘 **Companion & Coaching** | Practice partners, journaling assistants, accountability coaches, language tutors. |
| 🎓 **Education** | Flashcards, Socratic Q&A, reading helpers, quiz drills. |
| 📋 **Data Extraction** | Captures structured data through conversation: intake forms, surveys, KYC. |
| 🔧 **Tool Calling** | Calls APIs, MCP servers, web search mid conversation. |
| 🌍 **Multilingual** | Switches language, handles code switching, translates within a single conversation. |
| 👥 **Multi-Agent** | Specialized personas hand off to each other: triage to specialist, sales to support. |
| 📞 **Telephony & SIP** | Real phone calls. Inbound IVR, outbound surveys, warm transfer. |

When demos land, each category gets a section here with bold demo
names, one-line descriptions, and links to their folders.

## 📦 What you get with each demo

Every demo folder ships with the same six files: `agent.py`,
`pyproject.toml`, `.env.example`, a demo-specific `README.md`, a written
walkthrough (`blog.md`), and a short reel script (`reel.md`). Run the
demo on its own, or open the folder and read the walkthrough top to
bottom.

## 🤝 Adding a demo

See [CONTRIBUTING.md](CONTRIBUTING.md) for the folder layout, naming
convention, and the build budget.

## ⭐ Stargazers and contributors

[![Star History Chart](https://api.star-history.com/svg?repos=mahimairaja/awesome-voice-apps&type=Date)](https://star-history.com/#mahimairaja/awesome-voice-apps&Date)

<a href="https://github.com/mahimairaja/awesome-voice-apps/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=mahimairaja/awesome-voice-apps&max=40&columns=10&anon=0" alt="Contributors" />
</a>

## 📜 License

[Apache 2.0](LICENSE). Fork it, ship it, sell it.

## 🙌 Built by

[Mahimai Raja](https://mahimai.dev), founder of
[Mahimai AI](https://mahimai.ca), a voice ai company.
