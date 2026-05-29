# quick trivia

Voice agent that quizzes the caller with one short trivia question at a time,
evaluates the answer, and tracks a live score in the playground UI.

Category: Education.

## What it does

- Asks 10 trivia questions drawn from science, geography, history, pop culture, and sports.
- Waits for the caller's answer, then says correct or incorrect and gives the right answer when wrong.
- Mounts a Score card on the playground screen and updates it after every question.
- Announces the final score after question 10.

The agent uses one function tool: `score_answer`. The LLM evaluates correctness
and calls the tool with `was_correct` before moving to the next question.

## Try it

You need a free [LiveKit Cloud](https://cloud.livekit.io/) project (or a
self-hosted LiveKit server), plus API keys for OpenAI, Deepgram, and Cartesia.

```sh
git clone https://github.com/mahimairaja/awesome-voice-apps.git
cd awesome-voice-apps/demos/quick-trivia
cp ../../templates/livekit-base/.env.example .env
uv sync
uv run --no-project python agent.py download-files
uv run --no-project python agent.py dev
```

Then open [agents-playground.livekit.io](https://agents-playground.livekit.io)
and connect. Answer the questions and watch the score card update on the right.

## Recording

Coming after the demo is recorded.
