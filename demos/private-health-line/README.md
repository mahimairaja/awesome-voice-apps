# Private health line

A post-surgery check-in call for a fictional Canadian hospital where speech
recognition, the language model and the voice all run on your own GPU server.
No outside AI provider receives audio or text.

The agent asks six questions (name, procedure, pain, fever, wound, medication),
validates each answer in a tool, and routes the call to a nurse callback when an
answer is a warning sign. Every model request goes through a counting transport,
so the agent knows how many bytes it sent to which host and how long each stage
took. It publishes that, with the form, as a `PrivateLine` event on the `ui` data
topic.

| Stage | Model | Server |
|---|---|---|
| Speech to text | faster-whisper large-v3 turbo | [speaches](https://speaches.ai) |
| Language model | Qwen3-4B-Instruct-2507 | [vLLM](https://docs.vllm.ai) |
| Voice | Kokoro-82M | speaches |
| Voice activity | Silero VAD | this worker, on CPU |

## 1. Start the model server

On a machine with an NVIDIA GPU (24 GB is plenty), Docker and the NVIDIA
container toolkit, point a DNS name at it and run:

```sh
export ONPREM_DOMAIN=models.example.ca ONPREM_API_KEY=$(openssl rand -hex 32)
docker compose up -d
```

Caddy gets a TLS certificate for the domain and routes `/v1/audio/*` to speaches
and `/v1/chat/*` to vLLM. The first start downloads the models.

## 2. Run the agent

```sh
cp .env.example .env   # set ONPREM_BASE_URL=https://models.example.ca/v1 and the same key
uv sync
uv run python agent.py console
```

Run `uv run python agent.py dev` instead to connect a LiveKit client. For a
residency claim, also run LiveKit and this worker in the same region as the
model server; LiveKit Cloud and a worker elsewhere carry the audio through
their own regions.

This is a simulation. It gives no medical advice and saves nothing.
