# Pharmacy refill line

Refills a prescription by voice: medication, Rx number, date of birth, postal
code and pickup store. Each value is read back and confirmed before it counts,
sound-alike drugs (metformin and metoprolol) are caught, codes are spoken one
character at a time, and the call log is redacted before anything is written.

The prescription on file is generated per call and published as a bottle label
UI event; read your answers from it. Brands, patients and prescriptions are
fictional.

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

- `refill.py` `capture` and `confirm`: a value counts only after the caller
  confirms the read-back and it matches the file.
- `agent.py` `make_stt`: Deepgram Nova-3 keyterms for the drug formulary.
- `refill.py` `speakable`, used in `RefillLine.tts_node`: Rx numbers, postal
  codes and dates rewritten before TTS so they are read exactly.
- `refill.py` `redact`, used in `RefillLine.log_line`: dates, phone numbers,
  postal codes and emails masked before a line is logged.

Unit tests: `uv run --with pytest --with pytest-asyncio pytest ../../tests/pharmacy-refill`.
