# Delivery window call

An outbound agent calls a customer to confirm a furniture delivery window.
Answering machine detection decides who picked up. A person can press 1 to
confirm, press 2 to reschedule, or just say what they want. A voicemail gets a
short message and a hang-up.

## Credentials

`LIVEKIT_*`, `DEEPGRAM_API_KEY`, `OPENAI_API_KEY` and `CARTESIA_API_KEY`. For
real phone calls you also need a LiveKit outbound SIP trunk (Twilio, Telnyx or
another provider) and its id in `SIP_OUTBOUND_TRUNK_ID`. Calls cost money per
minute with your SIP provider.

## Run

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Place a call to a number you own (US and Canada, `+1` format):

```sh
lk dispatch create --new-room --agent-name delivery-window-call \
  --metadata '{"phone_number": "+15555550123"}'
```

Without a number, the agent waits in the room for a browser participant to
publish a microphone, which counts as picking up. Keypad presses then come from
`room.localParticipant.publishDtmf(code, digit)`, so you can try the whole flow
without a phone line. To hear the voicemail path, answer and read a greeting
such as "Hi, you've reached Sam, leave a message after the tone."

## How it works

- `create_sip_participant(..., wait_until_answered=True)` dials through the trunk.
  The `sip.callStatus` attribute reports ringing as it happens.
- `AMD` runs on the first thing the callee says and returns `human`,
  `machine-vm`, `machine-unavailable` or `uncertain`. It reuses the session's
  LLM and transcripts, so it adds no extra provider.
- Keypad presses arrive as `sip_dtmf_received` and take a fixed path with no
  LLM call. Spoken answers go through the same `confirm` and `reschedule`
  functions as tools, so both inputs end in the same state.
