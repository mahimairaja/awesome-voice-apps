# Billing de-escalation line

A caller disputes a $215 phone bill from Tellwave Mobile, a made-up carrier.
The agent scores the caller's frustration every turn, picks a de-escalation
move for its next reply, slows and softens its Cartesia voice as the call
heats up, and hands off to a billing specialist when the score says so.

What to try:

- Play an angry customer: "This is ridiculous, I've never paid $215 for a
  phone bill." Talk over the agent, repeat yourself, threaten to switch.
- Calm down when a charge is credited and watch the score fall.
- Ask for the full $105 back. The agent can credit $75 on its own, so it
  offers the specialist instead of promising what it cannot do.

How it is built:

- `FrustrationMeter` runs beside the LLM, not inside it. It scores each turn
  from words (anger, profanity, threats, asking for a person, repetition),
  timing (talking over the agent, words per second, long rants) and relief
  (thanks, a credit landing). One turn can rise at most 25 points.
- `pick_strategy` maps the strongest signal to one move: reflect back, name
  the feeling, offer a choice, take ownership, confirm the next step. The move
  is added to that turn's context only, in `on_user_turn_completed`.
- Each band has its own sonic-3 `speed` and `emotion`, applied with
  `tts.update_options` before the reply is spoken.
- The handoff is a rule in code: a score of 85, three turns above 70, or two
  requests for a person. The credit limit is enforced in `apply_credit`, not
  in the prompt.

## Credentials

LiveKit, Deepgram, OpenAI and Cartesia keys. See `.env.example`. Speed and
emotion controls need the `sonic-3` model.

## Run

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Run `uv run python agent.py dev` instead to connect a LiveKit client. The
`Bill`, `Mood` and `Handoff` UI events are optional; the call works without
them.

## Test

```sh
uv run python -m unittest test_agent
```

The tests cover the meter, the strategy picks, the credit limit and the
handoff packet. They need no keys.
