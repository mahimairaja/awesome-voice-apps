"""Offline test defaults for the hosted playground.

The worker builds LiveKit Inference clients, which read the LiveKit project
credentials when they are constructed. Tests never connect, so placeholders do.
"""

import os

os.environ.setdefault("LIVEKIT_API_KEY", "offline-test-only")
os.environ.setdefault("LIVEKIT_API_SECRET", "offline-test-only-secret-at-least-32-chars")
