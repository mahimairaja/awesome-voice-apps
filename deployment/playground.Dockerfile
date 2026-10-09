FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY demos/drive-thru-coffee/requirements-hosted.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY demos/drive-thru-coffee/*.py /app/demos/drive-thru-coffee/
COPY demos/talk-to-our-team/agent.py demos/talk-to-our-team/booking.py /app/demos/talk-to-our-team/
COPY demos/water-tracker/agent.py /app/demos/water-tracker/
COPY demos/tenant-rights/agent.py demos/tenant-rights/rag.py demos/tenant-rights/build_index.py /app/demos/tenant-rights/
COPY demos/tenant-rights/data/hud-resident-rights.md /app/demos/tenant-rights/data/
COPY demos/clinic-scheduler/agent.py /app/demos/clinic-scheduler/
COPY demos/claim-intake/agent.py /app/demos/claim-intake/
COPY demos/card-fraud-line/agent.py /app/demos/card-fraud-line/
RUN useradd --create-home worker
USER worker
RUN python -m livekit.agents download-files
CMD ["python", "demos/drive-thru-coffee/hosted.py", "start"]
