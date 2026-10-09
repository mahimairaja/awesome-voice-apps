# Bilingual 311 city line

A 311 line for the fictional City of Bellerive. Report a pothole, a missed
garbage pickup or a broken streetlight, or ask which day collection comes, in
English or French. Switch language mid-call and the agent follows, in the same
language and with a matching voice.

- Deepgram Nova-3 runs in `multi` mode, so one stream transcribes both
  languages and tags each final transcript with its language.
- `LanguageRouter` decides when to switch. One word ("oui", "okay") does not flip
  the call; three words, or two short turns in a row, do. Asking ("can we speak
  French?") always wins.
- One prompt serves both languages. Each turn adds a single line naming the
  language of service, and the Cartesia voice and language follow the same
  decision.
- Tickets use language-neutral category codes and record the language of
  service for follow-ups.

Everything is simulated: no ticket is filed and nothing is saved.

## Credentials

LiveKit, Deepgram, OpenAI and Cartesia keys. `CITY311_VOICE_EN` and
`CITY311_VOICE_FR` optionally override the Cartesia voice for each language.

## Run

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Try: "Hi, there's a pothole on Main Street." Then: "En fait, je préfère parler
français. C'est au coin de la 3e Avenue." Provider usage may cost money.
