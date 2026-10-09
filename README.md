<p align="center"><img src="assets/banner.svg" width="100%" alt="Awesome Voice Apps: small agents, working code, open contributions"></p>

Small, self-contained voice AI examples. Clone one, run it, make it yours.
Built with Python and LiveKit Agents.

[Examples](#examples) · [Contribute](CONTRIBUTING.md) · [Mahimai](https://mahimai.ca) · [Apache 2.0](LICENSE)

## Run an example

Install [uv](https://docs.astral.sh/uv/), then:

```sh
git clone https://github.com/mahimairaja/awesome-voice-apps.git
cd awesome-voice-apps/demos/drive-thru-coffee
cp .env.example .env
# Fill in .env with your own provider and LiveKit credentials.
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Console mode uses your microphone and speakers. For a LiveKit room, run
`uv run python agent.py dev` and connect a compatible client. Each example's
README lists its requirements and any extra setup. Provider usage may cost money.

<img src="assets/pipeline.svg" width="100%" alt="Speech in, agent logic and tools, speech out">

## Examples

| Example | What it does |
| --- | --- |
| [Card fraud line](demos/card-fraud-line/) | Verifies a caller about a flagged charge, hands off to a fraud specialist, and flags social engineering live. |
| [Build your own agent](demos/build-your-own-agent/) | One agent configured per tenant: change its brief, tools and voice mid-call over LiveKit RPC. |
| [Billing de-escalation](demos/billing-deescalation/) | Tracks an angry caller's frustration turn by turn, adapts its voice and tactics, and hands off to a person. |
| [Claim intake](demos/claim-intake/) | Takes an auto insurance claim by voice, validates each field, and files it. |
| [Clinic scheduler](demos/clinic-scheduler/) | Books a doctor appointment by voice, finds open slots, and handles reschedules. |
| [Delivery window call](demos/delivery-window-call/) | Calls a customer to confirm a delivery: detects voicemail, takes keypad presses, and reschedules by voice. |
| [Drive-thru coffee](demos/drive-thru-coffee/) | Takes a coffee order, modifies items mid-flow, totals the cart. |
| [Front desk interpreter](demos/front-desk-interpreter/) | Two languages, one front desk. Real-time, both directions. |
| [Manager approval](demos/manager-approval/) | Asks a manager to approve an over-limit refund mid-call, holds gracefully, and warm-transfers with the brief. |
| [Panel scribe](demos/panel-scribe/) | Labels each interviewer's voice live in a candidate debrief and turns it into an attributed scorecard. |
| [Post-op check-in](demos/postop-checkin/) | Calls a patient on day 3 after knee surgery; a fixed rule table, not the model, decides when to send them to the nurse or 911. |
| [Payer verification call](demos/payer-verification/) | Calls an insurer for a clinic: presses through the phone tree, waits on hold, and captures benefits from the rep. |
| [Quick trivia](demos/quick-trivia/) | Shows three trivia questions the caller can edit, then quizzes them one at a time and keeps score. |
| [Roadside dispatch](demos/roadside-dispatch/) | Roadside dispatcher: scores caller audio with Tyto, adapts when the line degrades, and re-confirms details captured over a bad line. |
| [Storm outage line](demos/storm-outage-line/) | Logs a power outage on a simulated landline or bad cell line, scores each reading, and shows what the telephony fixes win back. |
| [Support cost router](demos/support-cost-router/) | Routes each support turn to the cheapest model that can take it, answers repeat questions from a semantic cache, and prices the call against always using the big model. |
| [Sales copilot](demos/sales-copilot/) | A discovery call with an AI buyer while a silent second agent surfaces battle cards, a line to say and the next question, timed live. |
| [Talk to our team](demos/talk-to-our-team/) | GPT-Live sales conversation with request corrections and a guarded simulated calendar. |
| [Tenant rights](demos/tenant-rights/) | Answers US renter questions from public HUD guidance, names the source, and redirects to legal help when a question is out of scope. |
| [Water tracker](demos/water-tracker/) | Logs glasses of water by voice and tracks progress toward a daily goal. |

## Add yours

Copy [`templates/livekit-base`](templates/livekit-base/), build your agent,
and open a PR. Four essentials:

```text
agent.py   pyproject.toml   .env.example   README.md
```

A short reference README is enough. No blog, tutorial, UI manifest, or artwork
required. We write the website content separately. [Contribution steps](CONTRIBUTING.md).

Maintained by [Mahimai Raja](https://github.com/mahimairaja) at [Mahimai](https://mahimai.ca).
