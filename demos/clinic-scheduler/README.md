# clinic scheduler

Voice agent that books a doctor appointment by phone: finds open slots,
confirms a time with the caller, and handles reschedules mid-call.

Category: healthcare.

## What it does

- Presents six simulated appointment slots across four weekdays and three doctors.
- Mounts a List of open slots on the playground screen when the caller asks what is available.
- Books a slot after confirming the patient name and reason, mounting a Card with the appointment details.
- Reschedules by freeing the old slot, moving to the new one, and updating the Card.

The agent uses three function tools: `find_slots`, `book_appointment`, `reschedule`.
Slot state lives in the agent process. The playground renders what the tools publish.

## Try it

You need a free [LiveKit Cloud](https://cloud.livekit.io/) project (or
a self-hosted LiveKit server), plus an OpenAI API key.

```sh
git clone https://github.com/mahimairaja/awesome-voice-apps.git
cd awesome-voice-apps/demos/clinic-scheduler
cp .env.example .env
uv sync
uv run python agent.py dev
```

This demo runs on OpenAI for STT, LLM, and TTS, so its `.env.example` lists only
OPENAI_API_KEY and the three LIVEKIT_* keys.

Then open
[playground.mahimai.ca/demos/clinic-scheduler](https://playground.mahimai.ca/demos/clinic-scheduler)
and connect. Ask for Monday slots, pick one, give your name and reason, then
ask to reschedule and watch the card update.

## Recording

Coming after the demo is recorded.
