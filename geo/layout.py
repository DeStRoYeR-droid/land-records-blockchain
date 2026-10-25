"""
Where a new parcel goes when the Registrar does not draw its boundary.

Real land comes with a surveyed boundary. This demo network has no survey
department, so the Registrar can ask for "the next free plot" in a village:
the plot is laid out in that village's layout, a grid of rectangular plots in
rows (two rows back to back, then a 10 m road), rotated to the village's own
bearing so the map does not look like graph paper.

Stateless: the next free slot is worked out from the parcels already in the
geospatial database, so it survives a rebuild of that database from the ledger.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

from chaincode.landrec import geometry

PLOT_DEPTH_M = 40.0
ROAD_M = 10.0
ROW_LENGTH_M = 420.0
GAP_M = 0.0                 # neighbouring plots share a boundary

# Village layouts around Hanamkonda / Warangal, Telangana: (lng, lat) of the layout's first corner, bearing in degrees.
VILLAGES: dict[str, tuple[float, float, float]] = {
    "kazipet": (79.4872, 17.9612, 14.0),
    "hanamkonda": (79.5702, 18.0262, -9.0),
    "warangal": (79.6031, 17.9556, 22.0),
    "madikonda": (79.5281, 17.9497, 6.0),
    "hasanparthy": (79.5236, 18.0711, -17.0),
    "dharmasagar": (79.4412, 17.9918, 11.0),
    "elkathurthy": (79.4619, 18.0985, -4.0),
}
DISTRICT_CENTRE = (79.5596, 18.0115)


def village_frame(village: str) -> tuple[float, float, float]:
    key = village.strip().lower()
    if key in VILLAGES:
        return VILLAGES[key]
    h = hashlib.sha256(key.encode()).digest()                 # an unknown village: a stable spot within ~12 km
    dx, dy = (h[0] / 255 - 0.5) * 0.22, (h[1] / 255 - 0.5) * 0.22
    return DISTRICT_CENTRE[0] + dx, DISTRICT_CENTRE[1] + dy, (h[2] / 255 - 0.5) * 40


def _to_local(lng: float, lat: float, frame: tuple[float, float, float]) -> tuple[float, float]:
    """Metres along (u) and across (v) the village layout."""
    m_lng, m_lat = geometry.metres_per_degree(frame[1])
    x, y = (lng - frame[0]) * m_lng, (lat - frame[1]) * m_lat
    t = math.radians(frame[2])
    return x * math.cos(t) + y * math.sin(t), -x * math.sin(t) + y * math.cos(t)


def _to_lnglat(u: float, v: float, frame: tuple[float, float, float]) -> list[float]:
    t = math.radians(frame[2])
    x, y = u * math.cos(t) - v * math.sin(t), u * math.sin(t) + v * math.cos(t)
    return geometry.unproject([(x, y)], (frame[0], frame[1]))[0]


def row_top(row: int) -> float:
    """v coordinate of a row's front edge (rows run southwards from the layout's first corner)."""
    pair, back = divmod(row, 2)
    return -pair * (2 * PLOT_DEPTH_M + ROAD_M) - back * PLOT_DEPTH_M


def plot(village: str, row: int, u0: float, width: float) -> dict[str, Any]:
    frame = village_frame(village)
    top = row_top(row)
    corners = [(u0, top), (u0, top - PLOT_DEPTH_M), (u0 + width, top - PLOT_DEPTH_M), (u0 + width, top), (u0, top)]
    return geometry.parse_boundary({"type": "Polygon", "coordinates": [[_to_lnglat(u, v, frame) for u, v in corners]]})


def next_free_plot(village: str, area_sqm: float, existing: list[list[list[float]]]) -> dict[str, Any]:
    """A boundary enclosing `area_sqm` in the first row of the village layout with room for it.

    `existing` is the outer ring of every parcel already in this village."""
    frame = village_frame(village)
    width = max(4.0, float(area_sqm) / PLOT_DEPTH_M)
    ends: dict[int, float] = {}
    for ring in existing:                  # every row band a parcel reaches into is used up to its far end
        local = [_to_local(p[0], p[1], frame) for p in ring]
        v_lo, v_hi = min(v for _, v in local), max(v for _, v in local)
        u_hi = max(u for u, _ in local)
        for row in range(0, 400):
            top = row_top(row)
            if top - PLOT_DEPTH_M + 0.5 < v_hi and v_lo < top - 0.5:
                ends[row] = max(ends.get(row, 0.0), u_hi)
            if top < v_lo:                 # this row and every later one lies below the parcel
                break
    for row in range(0, 400):
        start = ends.get(row, 0.0) + (GAP_M if row in ends else 0.0)
        if start + width <= max(ROW_LENGTH_M, width) and (row in ends or start == 0.0):
            return plot(village, row, start, width)
    raise RuntimeError(f"the layout of {village} is full")
