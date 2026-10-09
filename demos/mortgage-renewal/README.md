# Mortgage renewal advisor

An advisor at a fictional bank explains a mortgage renewal: fixed versus
variable, the new payment, what happens if rates move. Nod along with "right"
or "mm-hm" and it keeps talking. Say "wait", "stop" or ask a question and it
stops mid-sentence and answers.

How it decides: voice activity alone never pauses the agent. The session's
`min_words` floor is set high, and a small backchannel gate reads the interim
transcript of any speech that overlaps the agent:

- only acknowledgement words ("mm-hm", "right", "got it"): keep talking;
- any other word: call `session.interrupt()` and answer;
- no words at all (a cough, a door): ignore it.

When the agent is cut off, LiveKit keeps only the words the caller heard in
the chat history, so the next reply never assumes the rest was said. The agent
publishes each scored overlap on the `ui` data topic (`Overlaps`) and the
renewal options (`Renewal`).

## Credentials

LiveKit (`LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`), Deepgram
(speech to text, with interim results), OpenAI (`gpt-4o-mini`) and Cartesia
(text to speech). Provider usage may cost money.

## Run

```sh
cp .env.example .env
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Use headphones in console mode, or the agent hears itself. Run
`uv run python agent.py dev` instead to connect a LiveKit client.
Rates and balances are illustrative; this is not financial advice.
