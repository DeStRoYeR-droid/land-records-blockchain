"""
A cadastral-style sketch of a parcel, drawn as SVG.

Deterministic: the same survey number always gives the same outline (derived
from its SHA-256), scaled by the recorded area. It is an illustration for the
dashboards, not a survey -- the boundaries text is the surveyor's record.
"""

from __future__ import annotations

import hashlib
import math
from html import escape


def parcel_svg(survey_no: str, area_sqm: float, village: str, district: str, boundaries: str | None,
               owner_status: str) -> str:
    h = hashlib.sha256(survey_no.encode()).digest()
    sides = 4 + h[0] % 3
    cx, cy = 200, 150
    radius = 70 + min(55, math.sqrt(max(area_sqm, 1)) * 0.9)
    pts = []
    for i in range(sides):
        ang = (2 * math.pi * i / sides) + (h[1] / 255) * 0.6
        r = radius * (0.75 + (h[2 + i] / 255) * 0.35)
        pts.append((cx + r * math.cos(ang), cy + r * 0.72 * math.sin(ang)))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    labels = {"N": (200, 26), "S": (200, 284), "E": (372, 154), "W": (28, 154)}
    bound = {}
    for part in (boundaries or "").split("|"):
        if ":" in part:
            k, v = part.split(":", 1)
            bound[k.strip()[:1].upper()] = v.strip()[:22]
    edge = "#b45309" if owner_status == "TRANSFER_PENDING" else "#0f766e"
    texts = "".join(
        f'<text x="{x}" y="{y}" text-anchor="middle" font-size="11" fill="#57534e">{k}'
        f'{(" · " + escape(bound[k])) if k in bound else ""}</text>' for k, (x, y) in labels.items())
    grid = "".join(f'<line x1="{x}" y1="40" x2="{x}" y2="262" stroke="#e7e5e4" stroke-width="1"/>' for x in range(40, 380, 30))
    grid += "".join(f'<line x1="40" y1="{y}" x2="360" y2="{y}" stroke="#e7e5e4" stroke-width="1"/>' for y in range(40, 270, 30))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 300" role="img" aria-label="Sketch of parcel {escape(survey_no)}">
<rect width="400" height="300" fill="#fafaf9"/>{grid}
<polygon points="{poly}" fill="{edge}" fill-opacity="0.12" stroke="{edge}" stroke-width="2.5" stroke-linejoin="round"/>
{"".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{edge}"/>' for x, y in pts)}
<text x="{cx}" y="{cy - 6}" text-anchor="middle" font-family="ui-monospace,monospace" font-size="15" font-weight="700" fill="#1c1917">{escape(survey_no)}</text>
<text x="{cx}" y="{cy + 14}" text-anchor="middle" font-size="12" fill="#44403c">{area_sqm:,.0f} sq.m · {escape(village)}, {escape(district)}</text>
{texts}
<text x="392" y="296" text-anchor="end" font-size="9" fill="#a8a29e">illustrative sketch -- not to scale</text>
</svg>"""
