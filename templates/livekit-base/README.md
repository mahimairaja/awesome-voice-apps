# LiveKit starter

A small voice assistant using Deepgram, OpenAI, Cartesia, and Silero.
Copy this folder into `demos/your-app`, rename the project in `pyproject.toml`,
and edit the assistant instructions or add tools.

```sh
cp .env.example .env
# Fill in the listed credentials.
uv sync
uv run python agent.py download-files
uv run python agent.py console
```

Console mode uses your microphone and speakers. Use `dev` instead of `console`
to connect a LiveKit client. Provider usage may cost money.

Keep the code, dependencies, environment example, and this short run reference.
No website metadata or article is required.
