# Payer verification call

Calls a health insurer for a clinic to verify a patient's eligibility and
benefits. The agent works through the payer's phone tree with keypad tones,
waits on hold, notices when a person picks up, and turns that person's answers
into a structured record: coverage, copay, deductible and how much is met,
coinsurance, prior authorization for an MRI, and a call reference number.

You play the payer's representative. The phone tree and hold music come from a
simulated payer (`payer.py`) whose menu order changes on every call, so the
agent has to listen to each menu. The made-up benefits to read out are logged
at the start of the call and published as a UI event. Brands, patients and
benefits are fictional.

## Run

From this directory, copy `.env.example` to `.env` and fill in the LiveKit,
Deepgram, OpenAI and Cartesia keys.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Stay quiet through the menus, then pick up
while the hold music plays: say something like "Harborline provider services,
this is Dana." Run `uv run python agent.py dev` instead to connect a LiveKit
client. Provider usage may cost money.

## Where the patterns live

- `PayerCaller.choose_keys` and `send_keys`: the model hears each menu and picks
  keys, which go out as DTMF events (`publish_dtmf`) with their tones. A
  deterministic route takes over if the model is slow.
- `PayerCaller.classify` and `payer.hold_signals`: while on hold, each
  transcript is classified as a recording or a person. Repeated announcements
  count as recordings whatever the model says.
- `payer.apply`: what the rep says is normalised, range-checked, cross-checked
  (met cannot exceed the deductible), kept with the words it came from, and
  corrections keep the old value.
- `finish_verification`: the call closes only when every required field is in.

To call a real payer, dial out through a SIP trunk (`create_sip_participant`)
and drop the simulated line: `publish_dtmf` already sends RFC 4733 events.

Unit tests: `uv run --with pytest --with pytest-asyncio pytest ../../tests/payer-verification`.
