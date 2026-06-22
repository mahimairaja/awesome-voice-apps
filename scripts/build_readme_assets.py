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

_VIGN = (
    '<radialGradient id="vign" cx="50%" cy="42%" r="80%">'
    f'<stop offset="55%" stop-color="{SCOPE}"/>'
    f'<stop offset="100%" stop-color="{SCOPE_EDGE}"/>'
    "</radialGradient>"
)
_HALO = (
    '<radialGradient id="halo" cx="50%" cy="50%" r="50%">'
    f'<stop offset="0" stop-color="{AMBER}" stop-opacity="0.22"/>'
    f'<stop offset="1" stop-color="{AMBER}" stop-opacity="0"/>'
    "</radialGradient>"
)
_BARS = (
    '<linearGradient id="bars" x1="0" y1="0" x2="1" y2="0">'
    f'<stop offset="0" stop-color="{AMBER_DEEP}"/>'
    f'<stop offset="0.4" stop-color="{AMBER}"/>'
    f'<stop offset="1" stop-color="{AMBER}"/>'
    "</linearGradient>"
)
_GLOW = (
    '<filter id="glow" x="-8%" y="-60%" width="116%" height="220%">'
    '<feGaussianBlur stdDeviation="2.6" result="b"/>'
    '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
    "</filter>"
)

# Cards and the pipeline strip use only the vignette and glow.
DEFS = "<defs>" + _VIGN + _GLOW + "</defs>"
# The banner additionally uses the halo and the bar gradient.
DEFS_BANNER = "<defs>" + _VIGN + _HALO + _BARS + _GLOW + "</defs>"


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


ICONS = {
    "clinic-scheduler": '<rect x="-14" y="-12" width="28" height="24" rx="3"/><path d="M-14 -4h28 M-7 -17v6 M7 -17v6"/><path d="M-5 4l3.5 3.5l6.5 -8"/>',
    "drive-thru-coffee": '<path d="M-10 -9h15v10a7.5 7.5 0 0 1 -15 0z"/><path d="M5 -5h4.5a4.5 4.5 0 0 1 0 9h-4.5"/><path d="M-6 -15q2.5 2.5 0 5 M1 -15q2.5 2.5 0 5"/>',
    "front-desk-interpreter": '<path d="M-15 -11h15a3 3 0 0 1 3 3v6a3 3 0 0 1 -3 3h-7l-5 4v-4h-3a3 3 0 0 1 -3 -3v-6a3 3 0 0 1 3 -3z"/><path d="M3 -1h11a3 3 0 0 1 3 3v6a3 3 0 0 1 -3 3v4l-5 -4"/>',
    "quick-trivia": '<circle r="14"/><path d="M-4.5 -5a4.5 4.5 0 1 1 5.5 5.2c-1.2 0.9 -1.2 1.8 -1.2 3.3"/><circle cx="0" cy="9" r="1.4" fill="'
    + AMBER
    + '"/>',
    "roadside-dispatch": '<path d="M-14 4h28 M-12 4l1 -5l3 -6h16l3 6l1 5"/><path d="M-7 -7h12"/><circle cx="-7" cy="5" r="3"/><circle cx="8" cy="5" r="3"/>',
    "tenant-rights": '<path d="M-13 -1l13 -12l13 12"/><path d="M-9 -3v13h18v-13"/><path d="M-2 10v-7h4v7"/>',
    "water-tracker": '<path d="M0 -14C8 -3 7 11 0 13C-7 11 -8 -3 0 -14Z"/><path d="M-5 5a5 5 0 0 0 10 0"/>',
}

# Fallback glyph (an info dot) so a demo added without an icon still renders.
DEFAULT_ICON = (
    '<circle r="13"/><path d="M0 -6v8"/><circle cx="0" cy="7" r="1.3" fill="' + AMBER + '"/>'
)


def icon_box(slug, x, y):
    glyph = ICONS.get(slug, DEFAULT_ICON)
    return (
        f'<g transform="translate({x},{y})">'
        '<rect x="-32" y="-32" width="64" height="64" rx="14" fill="none" '
        f'stroke="{AMBER}" stroke-opacity="0.45" stroke-width="1.5"/>'
        f'<g stroke="{AMBER}" stroke-width="2.1" fill="none" stroke-linecap="round" '
        f'stroke-linejoin="round" filter="url(#glow)">{glyph}</g>'
        "</g>"
    )


def render_card(slug, entry):
    category = _esc(entry["category"].upper())
    desc = _esc(truncate(entry["description"], 46))
    return (
        '<svg width="480" height="150" viewBox="0 0 480 150" '
        'xmlns="http://www.w3.org/2000/svg">'
        f"{DEFS}"
        '<rect width="480" height="150" rx="12" fill="url(#vign)"/>'
        '<rect x="0.5" y="0.5" width="479" height="149" rx="12" fill="none" '
        f'stroke="{AMBER}" stroke-opacity="0.16"/>'
        f"{graticule(0, 0, 480, 150, 48, 0.04)}"
        f"{icon_box(slug, 66, 75)}"
        f'<text x="124" y="62" font-family="{MONO}" font-size="21" '
        f'font-weight="700" fill="{SCOPE_TEXT}">{_esc(slug)}</text>'
        f'<text x="124" y="84" font-family="{MONO}" font-size="10.5" '
        f'fill="{AMBER}" letter-spacing="1.5">{category}</text>'
        f'<text x="124" y="110" font-family="{MONO}" font-size="12" '
        f'fill="{SCOPE_DIM}">{desc}</text>'
        "</svg>\n"
    )


def _voice_envelope(t):
    """Smooth, deterministic speech-like amplitude in ~[0,1] for bar t in [0,1]."""
    base = 0.45 + 0.55 * abs(math.sin(t * math.pi))
    detail = 0.55 + 0.45 * math.sin(t * 22 + 0.5) * 0.6 + 0.25 * math.sin(t * 7 + 1.2)
    return base * detail


def render_banner():
    cx, n, midy = 150, 58, 128
    gap = (1280 - 2 * cx) / (n - 1)
    bars = []
    for i in range(n):
        t = i / (n - 1)
        h = 18 + 150 * max(0.06, min(1.0, abs(_voice_envelope(t))))
        x = cx + i * gap
        bars.append(
            f'<rect x="{x - 3:.1f}" y="{midy - h / 2:.1f}" '
            f'width="6" height="{h:.1f}" rx="3" fill="url(#bars)"/>'
        )
    return (
        '<svg width="1280" height="320" viewBox="0 0 1280 320" '
        'xmlns="http://www.w3.org/2000/svg">'
        f"{DEFS_BANNER}"
        '<rect width="1280" height="320" fill="url(#vign)"/>'
        f"{graticule(0, 0, 1280, 320, 64, 0.05)}"
        '<ellipse cx="360" cy="250" rx="430" ry="150" fill="url(#halo)"/>'
        f'<g filter="url(#glow)">{"".join(bars)}</g>'
        f'<text x="60" y="252" font-family="{MONO}" font-size="60" '
        f'font-weight="700" fill="{SCOPE_TEXT}" letter-spacing="-1">awesome-voice-apps</text>'
        f'<text x="64" y="290" font-family="{MONO}" font-size="19" '
        f'fill="{SCOPE_DIM}">small voice agents you can clone and talk to</text>'
        f'<text x="60" y="46" font-family="{MONO}" font-size="13" fill="{AMBER}" '
        'letter-spacing="3">&#9679; REC 00:14</text>'
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
        f"{_node(170, 65, 'deepgram &#183; hear', ear)}"
        f"{_node(520, 65, 'llm &#183; think', chip)}"
        f"{_node(870, 65, 'cartesia &#183; speak', spk)}"
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
    pattern = re.compile(re.escape(GALLERY_START) + ".*?" + re.escape(GALLERY_END), re.DOTALL)
    if not pattern.search(text):
        raise ValueError("README gallery markers not found")
    return pattern.sub(GALLERY_START + "\n" + gallery_html + "\n" + GALLERY_END, text)


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
