<div align="center">

# 🎙️ Awesome Voice Apps

<p><strong>Voice AI agents you can clone, customize, and ship. One demo per day.</strong><br/>
Receptionists · Drive-thrus · Customer support · Companions · Multilingual · Telephony · Multi-agent</p>

<p>
<a href="https://github.com/mahimairaja/awesome-voice-apps/stargazers"><img src="https://img.shields.io/github/stars/mahimairaja/awesome-voice-apps?style=for-the-badge&logo=github&color=FFD700" alt="Stars"></a>
<a href="https://github.com/mahimairaja/awesome-voice-apps/network/members"><img src="https://img.shields.io/github/forks/mahimairaja/awesome-voice-apps?style=for-the-badge&logo=github&color=4FC3F7" alt="Forks"></a>
<a href="https://github.com/mahimairaja/awesome-voice-apps/graphs/contributors"><img src="https://img.shields.io/github/contributors/mahimairaja/awesome-voice-apps?style=for-the-badge&color=22C55E" alt="Contributors"></a>
<a href="LICENSE"><img src="https://img.shields.io/github/license/mahimairaja/awesome-voice-apps?style=for-the-badge&color=8B5CF6" alt="License"></a>
<img src="https://img.shields.io/github/last-commit/mahimairaja/awesome-voice-apps?style=for-the-badge&color=F97316" alt="Last Commit">
</p>

<p>
<a href="#-quick-start"><kbd> &nbsp; 🚀 Quick Start &nbsp; </kbd></a>
<a href="#-browse-demos"><kbd> &nbsp; 📂 Browse Demos &nbsp; </kbd></a>
<a href="#-latest-demo"><kbd> &nbsp; 🔥 Latest &nbsp; </kbd></a>
</p>

</div>

---

## 💡 Why this exists

Most voice AI tutorials stop at "agent says hello." This cookbook ships
a runnable voice demo every day, end to end.

- 🛠️ **Hand built, not curated.** Every demo is original work, tested before it ships.
- 🧪 **Runs in four commands.** Bring your own LiveKit and provider keys, talk to it.
- 🧠 **Real voice stack.** LiveKit Agents 1.x, Deepgram STT, OpenAI LLM, Cartesia TTS, Silero VAD, multilingual turn detection.
- 🌐 **Provider agnostic.** Swap Deepgram for Whisper, Cartesia for ElevenLabs, OpenAI for Anthropic with one config change.
- 💸 **Apache-2.0.** Fork it, ship it, sell it. No paywall, no signup, no telemetry.

> ⭐ **If this saves you time, [star the repo](https://github.com/mahimairaja/awesome-voice-apps/stargazers) so the next builder finds it.**

## 🚀 Quick Start

Run your first voice agent in under a minute:

```bash
git clone https://github.com/mahimairaja/awesome-voice-apps.git
cd awesome-voice-apps/templates/livekit-base
cp .env.example .env  # fill in six keys
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Open [agents-playground.livekit.io](https://agents-playground.livekit.io)
in a browser, connect, and talk to the agent.

You need a free [LiveKit Cloud](https://cloud.livekit.io) project (or a
self-hosted LiveKit server), API keys for OpenAI, Deepgram, and
Cartesia, and [uv](https://docs.astral.sh/uv) installed locally.

## 🔥 Latest Demo

_None yet. Demo #1 lands during M0.5._

| Date | Demo | Category | What it does |
|------|------|----------|--------------|
| _coming soon_ | | | |

## 📑 Table of Contents

<details open>
<summary><strong>10 categories. Click to expand.</strong></summary>

- [🏨 Receptionist & Booking](#-receptionist--booking)
- [🛒 Drive-thru & Ordering](#-drive-thru--ordering)
- [🎧 Customer Support](#-customer-support)
- [🧘 Companion & Coaching](#-companion--coaching)
- [📚 Education](#-education)
- [📋 Data Extraction](#-data-extraction)
- [🛠️ Tool Calling](#-tool-calling)
- [🌍 Multilingual](#-multilingual)
- [🤝 Multi-Agent](#-multi-agent)
- [📞 Telephony & SIP](#-telephony--sip)

</details>

## 📂 Browse Demos

### 🏨 Receptionist & Booking
*Voice agents that take inbound calls, qualify the caller, and schedule
or capture an appointment.*

_No demos yet. First one lands during M0.5._

### 🛒 Drive-thru & Ordering
*Voice agents that take an order, modify items mid-flow, run totals, and
hand off to a human or POS.*

_No demos yet._

### 🎧 Customer Support
*Status questions, returns, account changes, and warm handoff to a human
when the conversation hits its limits.*

_No demos yet._

### 🧘 Companion & Coaching
*Practice partners, journaling assistants, accountability coaches,
language tutors. Longer, more open conversations.*

_No demos yet._

### 📚 Education
*Voice agents that teach. Flashcards, Socratic Q&A, reading helpers,
quiz drills.*

_No demos yet._

### 📋 Data Extraction
*Structured data through conversation. Intake forms, surveys, KYC,
application questionnaires.*

_No demos yet._

### 🛠️ Tool Calling
*Voice agents that call out to tools, APIs, or MCP servers mid
conversation. Lookups, bookings, payments, web search.*

_No demos yet._

### 🌍 Multilingual
*Language switching, code switching, translation within a single
conversation.*

_No demos yet._

### 🤝 Multi-Agent
*Specialised personas handing off to each other. Triage to specialist,
sales to support, narrator to NPC.*

_No demos yet._

### 📞 Telephony & SIP
*Real phone calls via SIP. Inbound IVR, outbound surveys, warm transfer
to a human.*

_No demos yet._

## 🧰 Templates

There is one starter template, on purpose.

[`templates/livekit-base/`](templates/livekit-base/) is what every demo
copies from. Most demos change the agent instructions, add a
`@function_tool` or two, optionally swap a provider, and ship.

If a demo would need to materially extend the template, it becomes a
template extension under `templates/`, not a daily demo.

## 🤝 Contributing

This is the operator's daily ship cookbook, not yet a community
contribution magnet. To add a demo or fix a bug, see
[CONTRIBUTING.md](CONTRIBUTING.md) for the conventions: folder layout,
naming, build budget, commit style.

## 🙏 Built by

Created and maintained by **Mahimai Raja**, founder of
[Mahimai AI](https://mahimai.com), a voice agent agency. Each demo
doubles as a public artifact (the code), a tutorial (the blog), a reel
(the short form video), and a sales touchpoint. Every demo also seeds
[ShipVoice](https://shipvoice.dev), the paid voice AI starter kit for
indie hackers.

## 📜 License

[Apache 2.0](LICENSE). Fork it, ship it, sell it.
