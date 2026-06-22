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


def render_banner():
    wave = speech_wave("banner", 40, 1240, 168, 92, carrier=0.085)
    return (
        '<svg width="1280" height="320" viewBox="0 0 1280 320" '
        'xmlns="http://www.w3.org/2000/svg">'
        f"{DEFS}"
        '<rect width="1280" height="320" fill="url(#vign)"/>'
        f"{graticule(0, 0, 1280, 320, 64, 0.06)}"
        f'<line x1="0" y1="160" x2="1280" y2="160" stroke="{AMBER}" '
        'stroke-opacity="0.14"/>'
        f'<path d="{wave}" fill="none" stroke="url(#trace)" stroke-width="3" '
        'stroke-linecap="round" filter="url(#glow)"/>'
        f'<text x="48" y="40" font-family="{MONO}" font-size="14" fill="{AMBER}" '
        'letter-spacing="2">&#9679; LIVE</text>'
        f'<text x="1090" y="40" font-family="{MONO}" font-size="13" '
        f'fill="{SCOPE_DIM}">CH1 &#183; 20ms/div</text>'
        f'<text x="48" y="252" font-family="{MONO}" font-size="58" '
        f'font-weight="700" fill="{SCOPE_TEXT}" letter-spacing="-1">awesome-voice-apps</text>'
        f'<text x="52" y="289" font-family="{MONO}" font-size="19" '
        f'fill="{SCOPE_DIM}">a cookbook of small voice agents you can clone and talk to</text>'
        "</svg>\n"
    )


def _node(cx, cy, caption, glyph):
    return (
        f'<g transform="translate({cx},{cy})">'
        '<rect x="-34" y="-34" width="68" height="68" rx="12" fill="none" '
        f'stroke="{AMBER}" stroke-opacity="0.5" stroke-width="1.5"/>'
        f'<g stroke="{AMBER}" stroke-width="2.2" fill="none" stroke-linecap="round" '
        f'stroke-linejoin="round" filter="url(#glow)">{glyph}</g>'
        f'<text x="0" y="54" font-family="{MONO}" font-size="13" fill="{SCOPE_DIM}" '
        f'text-anchor="middle">{caption}</text>'
        "</g>"
    )


def render_pipeline():
    ear = '<path d="M-10 6 a12 12 0 1 1 16 4 c-4 3 -4 7 -1 10"/>'
    chip = (
        '<rect x="-11" y="-11" width="22" height="22" rx="4"/>'
        '<path d="M-11 -4h-6 M-11 4h-6 M11 -4h6 M11 4h6 '
        'M-4 -11v-6 M4 -11v-6 M-4 11v6 M4 11v6"/>'
    )
    spk = '<path d="M-11 -7 l17 7 l-17 7 z"/><path d="M10 -4 a7 7 0 0 1 0 8 M14 -8 a12 12 0 0 1 0 16"/>'
    seg1 = speech_wave("pipe-a", 240, 446, 65, 15, n=110, carrier=0.34)
    seg2 = speech_wave("pipe-b", 594, 800, 65, 15, n=110, carrier=0.34)
    return (
        '<svg width="1040" height="130" viewBox="0 0 1040 130" '
        'xmlns="http://www.w3.org/2000/svg">'
        f"{DEFS}"
        '<rect width="1040" height="130" rx="10" fill="url(#vign)"/>'
        f"{graticule(0, 0, 1040, 130, 52, 0.05)}"
        f'<line x1="204" y1="65" x2="836" y2="65" stroke="{AMBER}" '
        'stroke-opacity="0.3" stroke-width="1.5" stroke-dasharray="2 6"/>'
        f'<path d="{seg1}" fill="none" stroke="{AMBER}" stroke-width="2" filter="url(#glow)"/>'
        f'<path d="{seg2}" fill="none" stroke="{AMBER}" stroke-width="2" filter="url(#glow)"/>'
        f'{_node(170, 65, "deepgram &#183; hear", ear)}'
        f'{_node(520, 65, "llm &#183; think", chip)}'
        f'{_node(870, 65, "cartesia &#183; speak", spk)}'
        "</svg>\n"
    )


def render_gallery(slugs):
    cells = [
        f'<td width="50%"><a href="demos/{s}/">'
        f'<img src="assets/demos/{s}.svg" width="100%" alt="{s}"></a></td>'
        for s in slugs
    ]
    rows = []
    for i in range(0, len(cells), 2):
        pair = cells[i : i + 2]
        if len(pair) == 1:
            pair.append('<td width="50%"></td>')
        rows.append("<tr>\n" + "\n".join(pair) + "\n</tr>")
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def rewrite_gallery(text, gallery_html):
    pattern = re.compile(
        re.escape(GALLERY_START) + ".*?" + re.escape(GALLERY_END), re.DOTALL
    )
    if not pattern.search(text):
        raise ValueError("README gallery markers not found")
    return pattern.sub(
        GALLERY_START + "\n" + gallery_html + "\n" + GALLERY_END, text
    )


def _planned_files(catalog):
    slugs = list(catalog)
    files = {
        ASSETS / "banner.svg": render_banner(),
        ASSETS / "pipeline.svg": render_pipeline(),
    }
    for s in slugs:
        files[DEMO_ASSETS / f"{s}.svg"] = render_card(s, catalog[s])
    return slugs, files


def build(check=False):
    catalog = json.loads(CATALOG.read_text())
    slugs, files = _planned_files(catalog)
    readme = README.read_text()
    new_readme = rewrite_gallery(readme, render_gallery(slugs))

    if check:
        stale = [
            p.relative_to(REPO)
            for p, content in files.items()
            if not p.exists() or p.read_text() != content
        ]
        if new_readme != readme:
            stale.append(README.relative_to(REPO))
        return stale

    DEMO_ASSETS.mkdir(parents=True, exist_ok=True)
    for p, content in files.items():
        p.write_text(content)
    README.write_text(new_readme)
    return []


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build README banner + gallery assets.")
    ap.add_argument("--check", action="store_true", help="verify assets are current")
    args = ap.parse_args(argv)
    stale = build(check=args.check)
    if args.check and stale:
        print("stale README assets (run scripts/build_readme_assets.py):")
        for p in stale:
            print(f"  {p}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
