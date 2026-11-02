FROM ghcr.io/astral-sh/uv:0.9.5-python3.12-bookworm-slim
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
WORKDIR /app
# The demo's own dependency lock, so the eval agent runs the same libraries.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY agent.py demo.py ./
RUN uv run --frozen python agent.py download-files
CMD ["uv", "run", "--frozen", "python", "agent.py", "start"]
