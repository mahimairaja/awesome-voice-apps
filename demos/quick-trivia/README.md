# quick-trivia

Shows three trivia questions the caller can edit, then quizzes them one at a
time and keeps score.

Category: Education.

## What it does

- Mounts an editable quiz at the start: three questions and answers the caller
  can type over in the panel, or change by voice.
- Plays the quiz one question per turn, each shown on a Card as it is asked.
- Evaluates the caller's answer, accepts reasonable paraphrases, and reveals the
  answer on a miss.
- Mounts a Score card and updates it after every question, then announces the
  final score.

The agent uses three function tools: `set_question` (edit by voice during
setup), `ask_question` (show a question and start play), and `score_answer`
(record the result). Clickable edits arrive over a `ui_action` data channel: the
EditableTable panel publishes the saved rows and the agent updates its quiz.
Quiz state lives in the agent process; the playground renders what the agent
publishes.

## Try it

You need a free [LiveKit Cloud](https://cloud.livekit.io/) project (or a
self-hosted LiveKit server), plus API keys for OpenAI, Deepgram, and Cartesia.

```sh
git clone https://github.com/mahimairaja/awesome-voice-apps.git
cd awesome-voice-apps/demos/quick-trivia
cp .env.example .env
uv sync
uv run --no-project python agent.py dev
```

Then open
[playground.mahimai.ca/demos/quick-trivia](https://playground.mahimai.ca/demos/quick-trivia)
and connect. Edit a question in the panel (or tell the host to change one),
say start, then answer and watch the question and score cards update on the right.

## Recording

Coming after the demo is recorded.
