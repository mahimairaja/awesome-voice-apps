"""Generate the README banner, how-it-works strip, and per-demo gallery cards
from catalog.json. Deterministic: same catalog in, byte-identical SVGs out.

    python3 scripts/build_readme_assets.py           # regenerate assets + gallery
    python3 scripts/build_readme_assets.py --check    # verify nothing is stale
"""

import argparse
import hashlib
import json
import math
import random
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "catalog.json"
ASSETS = REPO / "assets"
DEMO_ASSETS = ASSETS / "demos"
README = REPO / "README.md"

SCOPE = "#0a0803"
SCOPE_EDGE = "#050402"
AMBER = "#ffb02e"
AMBER_DEEP = "#b45309"
SCOPE_TEXT = "#f4ead6"
SCOPE_DIM = "#bcab8e"
MONO = "ui-monospace, 'SF Mono', 'JetBrains Mono', Menlo, Consolas, monospace"

GALLERY_START = "<!-- gallery:start -->"
GALLERY_END = "<!-- gallery:end -->"

DEFS = (
    "<defs>"
    '<radialGradient id="vign" cx="50%" cy="44%" r="78%">'
    f'<stop offset="58%" stop-color="{SCOPE}"/>'
    f'<stop offset="100%" stop-color="{SCOPE_EDGE}"/>'
    "</radialGradient>"
    '<linearGradient id="trace" x1="0" y1="0" x2="1" y2="0">'
    f'<stop offset="0" stop-color="{AMBER_DEEP}"/>'
    f'<stop offset="0.35" stop-color="{AMBER}"/>'
    f'<stop offset="1" stop-color="{AMBER}"/>'
    "</linearGradient>"
    '<filter id="glow" x="-5%" y="-60%" width="110%" height="220%">'
    '<feGaussianBlur stdDeviation="3.2" result="b"/>'
    '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
    "</filter>"
    "</defs>"
)


def _seed(key):
    return int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)


def speech_wave(key, x0, x1, mid, amp, n=420, carrier=0.085):
    """A speech-like trace: a few Gaussian bursts modulating a carrier sine.
    Deterministic for a given key."""
    rng = random.Random(_seed(key))
    bursts = [
        (rng.uniform(x0, x1), rng.uniform(45, 130), rng.uniform(0.55, 1.0))
        for _ in range(rng.randint(5, 7))
    ]
    phase = rng.uniform(0, 2 * math.pi)
    pts = []
    for i in range(n + 1):
        x = x0 + (x1 - x0) * i / n
        env = 0.10
        for c, wdt, h in bursts:
            env += h * math.exp(-((x - c) ** 2) / (2 * wdt * wdt))
        env = min(env, 1.0)
        y = mid - amp * env * math.sin(carrier * x + phase)
        pts.append((x, y))
    return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)


def graticule(x0, y0, x1, y1, step, opacity):
    lines = []
    x = x0
    while x <= x1:
        lines.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y1}"/>')
        x += step
    y = y0
    while y <= y1:
        lines.append(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}"/>')
        y += step
    return (
        f'<g stroke="{AMBER}" stroke-opacity="{opacity}" stroke-width="1">'
        + "".join(lines)
        + "</g>"
    )


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def truncate(text, limit=52):
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_card(slug, entry):
    wave = speech_wave(slug, 24, 456, 44, 20, n=160, carrier=0.16)
    category = _esc(entry["category"].upper())
    desc = _esc(truncate(entry["description"]))
    return (
        '<svg width="480" height="150" viewBox="0 0 480 150" '
        'xmlns="http://www.w3.org/2000/svg">'
        f"{DEFS}"
        '<rect width="480" height="150" rx="10" fill="url(#vign)"/>'
        '<rect x="0.5" y="0.5" width="479" height="149" rx="10" fill="none" '
        f'stroke="{AMBER}" stroke-opacity="0.18"/>'
        f"{graticule(0, 0, 480, 150, 48, 0.05)}"
        f'<path d="{wave}" fill="none" stroke="{AMBER}" stroke-width="2" '
        'stroke-linecap="round" filter="url(#glow)"/>'
        f'<text x="24" y="98" font-family="{MONO}" font-size="22" '
        f'font-weight="700" fill="{SCOPE_TEXT}">{_esc(slug)}</text>'
        f'<text x="24" y="120" font-family="{MONO}" font-size="11" '
        f'fill="{AMBER}" letter-spacing="1.5">{category}</text>'
        f'<text x="24" y="138" font-family="{MONO}" font-size="12.5" '
        f'fill="{SCOPE_DIM}">{desc}</text>'
        "</svg>\n"
    )
