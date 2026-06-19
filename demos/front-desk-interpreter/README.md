# front-desk-interpreter

Live interpreter at a hotel front desk. A guest speaks any language, the desk
hears English, and replies come back in the guest's language, with live
captions on screen.

## What it does

- Interprets speech to speech on the Gemini Live API: no STT, no TTS, no VAD
  model, no turn detector. The leanest agent in the cookbook.
- English anchors the desk side; the guest's language is whatever the model
  last heard that was not English. Direction lives in the instructions, not in
  code.
- Pure conduit: it never answers questions or adds opinions, it only
  interprets, first person, questions stay questions.
- Streams live captions to the playground: each entry shows the spoken
  translation with the original muted beneath it.

This demo carries its own four-key `.env.example` (`GOOGLE_API_KEY` plus the
three LiveKit values) instead of the shared six-key template env. That is a
deliberate, operator-cleared exception: the whole voice stack is one Gemini
key. Playground visitors still paste only their three LiveKit values; the
Gemini key stays in the local agent `.env`.

## Run it

1. Fill `.env` with `GOOGLE_API_KEY` (from [Google AI Studio](https://aistudio.google.com/apikey))
   and your three LiveKit values.

   ```sh
   cp .env.example .env
   ```

2. Sync the environment.

   ```sh
   uv sync
   ```

3. Start the agent.

   ```sh
   uv run python agent.py dev
   ```

Then open https://playground.mahimai.ca/demos/front-desk-interpreter, paste
your three LiveKit values, and start talking. Try opening in English, then
switching to any other language mid-call.

No download-files step: there are no local VAD or turn-detector weights to
fetch.

## Two-party call

The two speakers can also be on their own devices. Once connected, use "invite a
guest" to copy a join link, then open it in another tab or send it to a second
device. That person joins the same room on camera, and the interpreter relays
both directions, switching to whoever is speaking. Use headphones (or two
devices) so the speakers do not echo into the mics.

Before connecting, pick the desk-side language (default English) from the target
language dropdown. Everything the guest says is rendered into that language, and
that language is rendered back into whatever the guest speaks. Changing it means
reconnecting, since the interpreter's instructions are fixed for the session.

## Recording

Coming after the demo is recorded.
