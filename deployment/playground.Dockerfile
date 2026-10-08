FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY demos/drive-thru-coffee/requirements-hosted.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY demos/drive-thru-coffee/*.py /app/demos/drive-thru-coffee/
COPY demos/talk-to-our-team/agent.py demos/talk-to-our-team/booking.py /app/demos/talk-to-our-team/
COPY demos/water-tracker/agent.py /app/demos/water-tracker/
COPY demos/roadside-dispatch/agent.py demos/roadside-dispatch/health.py /app/demos/roadside-dispatch/
# Bake the Tyto audio-health model (about 20 MB) into the image.
RUN python -c "import aic_sdk as a; a.Model.download('tyto-l-16khz', '/app/demos/roadside-dispatch/models')"
RUN useradd --create-home worker
USER worker
RUN python -m livekit.agents download-files
CMD ["python", "demos/drive-thru-coffee/hosted.py", "start"]
