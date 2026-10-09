# Medical bill explainer

Member services for Kestrel Health, a made-up health plan. Mid-call the member
sends a photo of a confusing bill or explanation of benefits. The agent reads
it, explains each line, catches billing errors and opens a dispute when the
member agrees.

What makes it work:

- **The file arrives inside the call.** The member's app sends the image on a
  LiveKit byte stream (topic `bill-upload`). No upload endpoint, no storage
  bucket, no signed URL: the agent that is talking receives it directly. The
  handler checks the sender, type and size, and reads at most three bills a call.
- **Vision fills a schema, it does not answer.** `gpt-4.1-mini` copies each
  line into a strict JSON schema (date, code, billed, allowed, plan paid, you
  owe, remark). It is told never to calculate.
- **Code finds the errors.** `audit()` checks for duplicate lines, in-network
  balance billing (you owe more than allowed minus plan paid) and totals that
  do not add up. The model explains findings; it never decides them.
- **Answers are grounded to lines.** The LLM's context lists which lines exist
  but none of their amounts. To say a number it must call `show_lines`, which
  returns that line's figures and highlights it on the member's screen.

## Credentials

LiveKit, OpenAI (voice LLM and `gpt-4.1-mini` vision), Deepgram and Cartesia.
See `.env.example`.

## Run

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py dev
```

Join the room from a LiveKit client with a microphone, for example the
[Agents Playground](https://agents-playground.livekit.io). Then send a bill
into that room from a second terminal:

```sh
uv run python send_bill.py <room-name> samples/urgent-care-eob.png
```

The three samples in `samples/` are fictional: a duplicate lab charge, an MRI
balance bill and a clean physical therapy bill that should pass every check.
Use made-up documents only; do not send a real bill with someone's health data.

Optional UI events (`BillStatus`, `BillRead`, `BillFocus`, `BillDispute` on
topic `ui`) need a compatible client; the call works without them. Images are
never written to disk and are sent to OpenAI with `store=False`. Provider usage
may cost money.
