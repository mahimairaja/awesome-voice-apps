# Post-op check-in

Calls a patient on day 3 after a total knee replacement and runs a fixed
protocol: breathing, calf, temperature, incision and pain. The model asks the
questions and turns each answer into a typed tool call. A rule table in
`protocol.py` decides what happens: chest pain or shortness of breath stops the
call with a 911 script, a fever, a calf symptom, a wound sign or uncontrolled
pain goes to the nurse line with an SBAR handoff note, and everything else is
routine.

The hospital, patient and protocol are fictional and not medical advice. The
check-in state is published as a `PostOp` UI event: the protocol path, each
answer with the patient's words, the rules that fired, and the handoff note.

## Run

From this directory, copy `.env.example` to `.env` and fill in the LiveKit,
Deepgram, OpenAI and Cartesia keys.

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use your microphone and speakers. Run `uv run python agent.py dev` instead to
connect a LiveKit client. Optional UI events need a compatible client; the voice
conversation runs without website metadata. Provider usage may cost money.

## Where the patterns live

- `protocol.py` `RULES` and `record`: the rule table, evaluated in code on every
  answer. The tier is the most severe rule that fired; no tool lowers it.
- `protocol.py` `STEPS` and `current_step`: emergency screens first, and the next
  question always comes from the protocol order, even when the patient answers
  out of order.
- `protocol.py` `record`: a corrected answer is kept with the earlier one, and a
  rule that fired stays fired.
- `protocol.py` `handoff`: the SBAR note the nurse receives, built from the
  recorded answers rather than a model summary.
- `agent.py` `CheckInCall`: one typed tool per question, so the model extracts
  values (`reading: float | None`, `drainage: Literal[...]`) and never decides.

Unit tests: `uv run --with pytest --with pytest-asyncio pytest ../../tests/postop-checkin`.
