FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements-hosted.txt .
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir -r requirements-hosted.txt \
    && apt-get purge -y --auto-remove git && rm -rf /var/lib/apt/lists/*
COPY agent.py hosted.py trivia.py ./
RUN useradd --create-home worker
USER worker
RUN python -m livekit.agents download-files
CMD ["python", "hosted.py", "start"]
