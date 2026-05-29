# quick-trivia

Quizzes the caller with one short trivia question at a time and keeps score.

Category: Education.

## What it does

- Asks 10 trivia questions in order, one per turn.
- Evaluates the caller's answer and accepts reasonable paraphrases.
- Reveals the correct answer on a miss.
- Mounts a Score card in the playground UI and updates it after every question.
- Announces the final score after question ten.

The agent uses one function tool: `score_answer`. Score state lives in the
agent process. The playground only renders what the tool publishes.

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

Then open
[playground.mahimai.ca/demos/quick-trivia](https://playground.mahimai.ca/demos/quick-trivia)
and connect. Answer the questions and watch the score card update on the right.

## Recording

Coming after the demo is recorded.
