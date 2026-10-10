FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
# libopus: the concierge's Spatius avatar sends the agent's audio as Ogg Opus.
# git: OpenRTC installs from a pinned commit; it is removed after the install.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libopus0 git \
    && rm -rf /var/lib/apt/lists/*
COPY playground/requirements-hosted.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove git
COPY playground/*.py /app/playground/
COPY demos/drive-thru-coffee/agent.py /app/demos/drive-thru-coffee/
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
COPY demos/storm-outage-line/agent.py demos/storm-outage-line/phone_line.py demos/storm-outage-line/scoring.py /app/demos/storm-outage-line/
COPY demos/fair-cancellation/agent.py demos/fair-cancellation/policy.py /app/demos/fair-cancellation/
COPY demos/medical-bill-explainer/agent.py /app/demos/medical-bill-explainer/
COPY demos/pronunciation-coach/agent.py demos/pronunciation-coach/coach.py /app/demos/pronunciation-coach/
COPY demos/postop-checkin/agent.py demos/postop-checkin/protocol.py /app/demos/postop-checkin/
COPY demos/manager-approval/agent.py /app/demos/manager-approval/
COPY demos/billing-deescalation/agent.py /app/demos/billing-deescalation/
COPY demos/returning-caller/agent.py demos/returning-caller/memory.py /app/demos/returning-caller/
COPY demos/support-cost-router/agent.py demos/support-cost-router/router.py /app/demos/support-cost-router/
COPY demos/payer-verification/agent.py demos/payer-verification/payer.py /app/demos/payer-verification/
COPY demos/loan-callback/agent.py demos/loan-callback/application.py /app/demos/loan-callback/
COPY demos/private-health-line/agent.py /app/demos/private-health-line/
COPY demos/phone-tree-router/agent.py demos/phone-tree-router/routing.py /app/demos/phone-tree-router/
COPY demos/sales-copilot/agent.py demos/sales-copilot/copilot.py /app/demos/sales-copilot/
COPY demos/agent-stress-test/agent.py demos/agent-stress-test/stresstest.py /app/demos/agent-stress-test/
COPY demos/hotel-concierge/agent.py /app/demos/hotel-concierge/
RUN useradd --create-home worker
USER worker
RUN python -m livekit.agents download-files
CMD ["python", "playground/hosted.py", "start"]
