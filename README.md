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
uv run --no-project python agent.py download-files
uv run --no-project python agent.py dev
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
├── catalog.json             playground metadata for every shipped demo
├── templates/
│   └── livekit-base/        the starter every demo copies from
├── demos/
│   └── <slug>/              one folder per demo, two files
└── LICENSE
```

## 🚧 Up next

The first demo has shipped. Up next: TBD. Open a discussion to suggest
the next small voice agent.

| Slug | What it does | Category | Status | Recording |
| --- | --- | --- | :---: | --- |
| [`drive-thru-coffee`](demos/drive-thru-coffee/) | Takes a coffee order, modifies items, runs totals, hands off | restaurant | 🟢 Shipped | Recording coming |

## 📚 Categories (19 industries)

Each demo is tagged with the one industry it serves. Single lowercase
word, so the tag is short and URL-friendly. Playground metadata lives in
the root `catalog.json`.

| Tag | The industry it serves |
| --- | --- |
| 🩺 **healthcare** | Clinics, providers, pharmacy, patient intake and follow-up. |
| ⚖️ **legal** | Law firms, intake, scheduling, document Q&A. |
| 💳 **finance** | Banking, lending, payments, account servicing. |
| 🏠 **realestate** | Listings, showings, tenant and buyer enquiries. |
| 🛎️ **hospitality** | Hotels, bars, cafes, front-desk and concierge. |
| 🥤 **restaurant** | QSR and dining: ordering, drive-thru, reservations. |
| 🚗 **automotive** | Dealers, service centres, parts, roadside. |
| 🎓 **education** | Tutoring, drills, quizzes, course support. |
| 🛍️ **retail** | Storefronts, order status, returns, product help. |
| 🧑‍💼 **recruiting** | Screening, scheduling interviews, candidate Q&A. |
| 🏗️ **construction** | Trades, quotes, dispatch, site coordination. |
| ✈️ **travel** | Trip booking, changes, itineraries, support. |
| 🏋️ **fitness** | Gyms, coaching, habits, wellness tracking. |
| 💅 **beauty** | Salons, spas, bookings, product advice. |
| 📦 **logistics** | Shipping, tracking, warehousing, dispatch. |
| 🛡️ **insurance** | Quotes, claims, policy servicing, FNOL. |
| 🤝 **nonprofit** | Donations, volunteering, member and outreach lines. |
| 🏛️ **gov** | Government and public sector services and info lines. |
| 🎬 **media** | Publishing, streaming, events, audience support. |

### restaurant

- **[drive-thru-coffee](demos/drive-thru-coffee/)** - Takes a coffee order, modifies items mid-flow, runs totals, hands off to pickup.

### fitness

- **[water-tracker](demos/water-tracker/)** - Logs glasses of water by voice and tracks progress toward a daily goal.

### education

- **[quick-trivia](demos/quick-trivia/)** - Quizzes the caller with one trivia question at a time and keeps score.

## 📦 What you get with each demo

Every demo folder ships with the same two files: `agent.py` and
`requirements.txt`. Run the demo on its own, or scan `catalog.json` to
see the title, category, credentials, recording link, and playground UI
components.

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
