"""
Parcel boundaries for the chaincode: parse, validate and measure a GeoJSON polygon.

Pure Python and deterministic (no clock, no randomness, no libraries), because
every endorsing peer must compute the same result. The geospatial database
(geo/) imports these same functions, so the area the ledger records and the
area the map shows can never disagree.

A boundary is a GeoJSON Polygon with one exterior ring of [longitude, latitude]
positions in WGS 84 (EPSG:4326), as RFC 7946 prescribes. Areas and lengths are
computed in a local tangent plane centred on the parcel, which is accurate to a
few parts in a million at the scale of a land parcel (metres to a kilometre).
"""

from __future__ import annotations

import json
import math
from typing import Any

# India's mainland and islands, generously: a boundary outside this box is a typo.
LNG_RANGE = (68.0, 98.0)
LAT_RANGE = (6.0, 38.0)
MAX_VERTICES = 64
COORD_DECIMALS = 7          # ~1 cm


def metres_per_degree(lat: float) -> tuple[float, float]:
    """(metres per degree of longitude, metres per degree of latitude) at this latitude (WGS 84 series)."""
    phi = math.radians(lat)
    m_lat = 111132.92 - 559.82 * math.cos(2 * phi) + 1.175 * math.cos(4 * phi) - 0.0023 * math.cos(6 * phi)
    m_lng = 111412.84 * math.cos(phi) - 93.5 * math.cos(3 * phi) + 0.118 * math.cos(5 * phi)
    return m_lng, m_lat


def bbox(ring: list[list[float]]) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def project(ring: list[list[float]], origin: tuple[float, float] | None = None) -> list[tuple[float, float]]:
    """Positions in metres east/north of `origin` (default: the centre of the ring's bounding box)."""
    if origin is None:
        x0, y0, x1, y1 = bbox(ring)
        origin = ((x0 + x1) / 2, (y0 + y1) / 2)
    m_lng, m_lat = metres_per_degree(origin[1])
    return [((p[0] - origin[0]) * m_lng, (p[1] - origin[1]) * m_lat) for p in ring]


def unproject(points: list[tuple[float, float]], origin: tuple[float, float]) -> list[list[float]]:
    m_lng, m_lat = metres_per_degree(origin[1])
    return [[round(origin[0] + x / m_lng, COORD_DECIMALS), round(origin[1] + y / m_lat, COORD_DECIMALS)]
            for x, y in points]


def signed_area(points: list[tuple[float, float]]) -> float:
    """Shoelace formula; positive when the ring runs counter-clockwise."""
    return sum(points[i][0] * points[i + 1][1] - points[i + 1][0] * points[i][1] for i in range(len(points) - 1)) / 2


def area_sqm(ring: list[list[float]]) -> float:
    return abs(signed_area(project(ring)))


def perimeter_m(ring: list[list[float]]) -> float:
    pts = project(ring)
    return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def centroid(ring: list[list[float]]) -> list[float]:
    """Area-weighted centroid, as [lng, lat]."""
    x0, y0, x1, y1 = bbox(ring)
    origin = ((x0 + x1) / 2, (y0 + y1) / 2)
    pts = project(ring, origin)
    a = signed_area(pts)
    if abs(a) < 1e-9:
        return [round(origin[0], COORD_DECIMALS), round(origin[1], COORD_DECIMALS)]
    cx = sum((pts[i][0] + pts[i + 1][0]) * (pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1])
             for i in range(len(pts) - 1)) / (6 * a)
    cy = sum((pts[i][1] + pts[i + 1][1]) * (pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1])
             for i in range(len(pts) - 1)) / (6 * a)
    return unproject([(cx, cy)], origin)[0]


def _segments_cross(p1, p2, q1, q2) -> bool:
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-12 else (1 if v > 0 else -1)

    def on_segment(a, b, c):
        return min(a[0], b[0]) - 1e-12 <= c[0] <= max(a[0], b[0]) + 1e-12 and \
            min(a[1], b[1]) - 1e-12 <= c[1] <= max(a[1], b[1]) + 1e-12

    o1, o2, o3, o4 = orient(p1, p2, q1), orient(p1, p2, q2), orient(q1, q2, p1), orient(q1, q2, p2)
    if o1 != o2 and o3 != o4:
        return True
    return (o1 == 0 and on_segment(p1, p2, q1)) or (o2 == 0 and on_segment(p1, p2, q2)) or \
        (o3 == 0 and on_segment(q1, q2, p1)) or (o4 == 0 and on_segment(q1, q2, p2))


def is_simple(ring: list[list[float]]) -> bool:
    """No edge crosses or touches a non-adjacent edge."""
    pts = project(ring)
    n = len(pts) - 1
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue
            if _segments_cross(pts[i], pts[i + 1], pts[j], pts[j + 1]):
                return False
    return True


def parse_boundary(text: str | dict[str, Any]) -> dict[str, Any]:
    """Validate a GeoJSON Polygon and return it normalised (rounded, closed, counter-clockwise).

    Raises ValueError with a message an officer can act on."""
    try:
        geo = json.loads(text) if isinstance(text, str) else text
    except ValueError:
        raise ValueError("boundary is not valid JSON")
    if isinstance(geo, dict) and geo.get("type") == "Feature":
        geo = geo.get("geometry")
    if not isinstance(geo, dict) or geo.get("type") != "Polygon":
        raise ValueError("boundary must be a GeoJSON Polygon")
    rings = geo.get("coordinates")
    if not isinstance(rings, list) or len(rings) != 1:
        raise ValueError("boundary must have exactly one ring (no holes)")
    ring = rings[0]
    if not isinstance(ring, list) or len(ring) < 4:
        raise ValueError("boundary needs at least 3 corners (4 positions, the first repeated at the end)")
    out: list[list[float]] = []
    for pos in ring:
        if not isinstance(pos, list) or len(pos) < 2:
            raise ValueError("each boundary position must be [longitude, latitude]")
        try:
            lng, lat = float(pos[0]), float(pos[1])
        except (TypeError, ValueError):
            raise ValueError("boundary positions must be numbers")
        if not (math.isfinite(lng) and math.isfinite(lat)):
            raise ValueError("boundary positions must be finite numbers")
        if not (LNG_RANGE[0] <= lng <= LNG_RANGE[1] and LAT_RANGE[0] <= lat <= LAT_RANGE[1]):
            raise ValueError(f"boundary position [{lng}, {lat}] is outside India (expected [longitude, latitude])")
        p = [round(lng, COORD_DECIMALS), round(lat, COORD_DECIMALS)]
        if out and p == out[-1]:
            continue
        out.append(p)
    if out[0] != out[-1]:
        out.append(list(out[0]))
    if len(out) < 4:
        raise ValueError("boundary needs at least 3 distinct corners")
    if len(out) - 1 > MAX_VERTICES:
        raise ValueError(f"boundary has more than {MAX_VERTICES} corners")
    if not is_simple(out):
        raise ValueError("boundary edges cross each other")
    if signed_area(project(out)) < 0:
        out = list(reversed(out))
    if area_sqm(out) < 1:
        raise ValueError("boundary encloses no area")
    return {"type": "Polygon", "coordinates": [out]}


def describe(boundary: dict[str, Any]) -> dict[str, Any]:
    ring = boundary["coordinates"][0]
    x0, y0, x1, y1 = bbox(ring)
    return {"boundaryAreaSqm": round(area_sqm(ring), 2), "perimeterM": round(perimeter_m(ring), 2),
            "centroid": centroid(ring), "bbox": [x0, y0, x1, y1], "corners": len(ring) - 1}
