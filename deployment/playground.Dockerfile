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
COPY demos/panel-scribe/agent.py /app/demos/panel-scribe/
COPY demos/front-desk-interpreter/agent.py /app/demos/front-desk-interpreter/
COPY demos/pharmacy-refill/agent.py demos/pharmacy-refill/refill.py /app/demos/pharmacy-refill/
COPY demos/city-311/agent.py /app/demos/city-311/
COPY demos/voice-checkout/agent.py /app/demos/voice-checkout/
COPY demos/furnace-repair/agent.py demos/furnace-repair/turns.py /app/demos/furnace-repair/
COPY demos/card-fraud-line/agent.py /app/demos/card-fraud-line/
COPY demos/interview-coach/agent.py /app/demos/interview-coach/
COPY demos/mortgage-renewal/agent.py /app/demos/mortgage-renewal/
COPY demos/router-rescue/agent.py /app/demos/router-rescue/
COPY demos/build-your-own-agent/agent.py /app/demos/build-your-own-agent/
COPY demos/flight-rebooking/agent.py /app/demos/flight-rebooking/
COPY demos/returns-desk-qa/agent.py /app/demos/returns-desk-qa/
COPY demos/delivery-window-call/agent.py /app/demos/delivery-window-call/
RUN useradd --create-home worker
USER worker
RUN python -m livekit.agents download-files
CMD ["python", "demos/drive-thru-coffee/hosted.py", "start"]
