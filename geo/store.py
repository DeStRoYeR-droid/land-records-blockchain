"""
The geospatial database: every parcel's boundary in an OGC GeoPackage with an R*Tree index.

The ledger is the source of truth; this file is an index built from it (the
gateway projects every ParcelRegistered / TransferApproved / ... event into
it, and `./landchain.sh geo-rebuild` rebuilds it from scratch). It answers the
questions a key-value ledger cannot answer quickly:

  in_bbox      every parcel inside the map window          (R*Tree, then exact intersection)
  near         parcels within a radius of a point, nearest first
  at           the parcel under a point                     (point in polygon)
  neighbours   parcels sharing a boundary with, or within N metres of, a parcel
  overlaps     parcels whose boundaries overlap a parcel or a proposed boundary
  search       text + area + status filters, optionally inside a window

Two-step query plan, the way spatial databases work: the R*Tree finds
candidates whose bounding boxes qualify (fast, approximate), then Shapely
tests the exact geometry in a local metric projection (exact, slower).
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from shapely.geometry import Point, Polygon, box

from chaincode.landrec import geometry

from . import gpkg, layout

TABLE = "parcels"
COLUMNS = """
    survey_no TEXT NOT NULL UNIQUE, village TEXT, district TEXT, status TEXT, area_sqm REAL,
    boundary_area_sqm REAL, perimeter_m REAL, centroid_lng REAL, centroid_lat REAL, owner_initials TEXT,
    owners INTEGER DEFAULT 1, transfers INTEGER DEFAULT 0, registered_at TEXT, last_transfer_at TEXT,
    updated_at TEXT, pending_application TEXT, ledger_tx TEXT
"""


def _initials(name: str) -> str:
    return " ".join(f"{p[0].upper()}." for p in str(name or "").split() if p[:1].isalpha())


class GeoStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.rtree = gpkg.create_schema(self.db, TABLE, COLUMNS, "LandChain parcels (built from the ledger)")
        self.index = gpkg.index_table(self.db, TABLE)

    # ------------------------------------------------------------------ writing

    def upsert(self, parcel: dict[str, Any], chain_owners: int | None = None) -> bool:
        """Insert or update one parcel from its ledger record. Parcels without a boundary are not indexed."""
        boundary = parcel.get("boundary")
        if not boundary:
            return False
        shape = geometry.describe(boundary)
        x0, y0, x1, y1 = shape["bbox"]
        transfers = (chain_owners - 1) if chain_owners else None
        row = (gpkg.gpkg_blob(boundary["coordinates"]), parcel["surveyNo"], parcel.get("village"), parcel.get("district"),
               parcel.get("status"), parcel.get("areaSqm"), shape["boundaryAreaSqm"], shape["perimeterM"],
               shape["centroid"][0], shape["centroid"][1], _initials(parcel.get("ownerName", "")),
               parcel.get("registeredAt"), (parcel.get("lastTransfer") or {}).get("at"), parcel.get("updatedAt"),
               parcel.get("pendingApplication"))
        with self.lock:
            cur = self.db.execute("SELECT fid, owners, transfers FROM parcels WHERE survey_no=?", (parcel["surveyNo"],)).fetchone()
            if cur:
                owners = chain_owners or cur["owners"]
                self.db.execute(
                    "UPDATE parcels SET geom=?, survey_no=?, village=?, district=?, status=?, area_sqm=?, boundary_area_sqm=?,"
                    " perimeter_m=?, centroid_lng=?, centroid_lat=?, owner_initials=?, registered_at=?, last_transfer_at=?,"
                    " updated_at=?, pending_application=?, owners=?, transfers=? WHERE fid=?",
                    (*row, owners, transfers if transfers is not None else cur["transfers"], cur["fid"]))
                fid = cur["fid"]
                self.db.execute(f"DELETE FROM {self.index} WHERE id=?", (fid,))
            else:
                fid = self.db.execute(
                    "INSERT INTO parcels (geom, survey_no, village, district, status, area_sqm, boundary_area_sqm, perimeter_m,"
                    " centroid_lng, centroid_lat, owner_initials, registered_at, last_transfer_at, updated_at,"
                    " pending_application, owners, transfers) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*row, chain_owners or 1, transfers or 0)).lastrowid
            self.db.execute(f"INSERT INTO {self.index} (id, minx, maxx, miny, maxy) VALUES (?,?,?,?,?)", (fid, x0, x1, y0, y1))
            gpkg.update_extent(self.db, TABLE)
            self.db.commit()
        return True

    def clear(self) -> None:
        with self.lock:
            self.db.execute("DELETE FROM parcels")
            self.db.execute(f"DELETE FROM {self.index}")
            self.db.commit()

    # ------------------------------------------------------------------ reading helpers

    def _rows(self, where: str = "1", params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(f"SELECT * FROM parcels WHERE {where}", tuple(params)).fetchall()

    def _candidates(self, x0: float, y0: float, x1: float, y1: float, extra: str = "", params: tuple = ()) -> list[sqlite3.Row]:
        """Step 1 of every spatial query: bounding boxes that intersect the window, via the R*Tree."""
        sql = (f"SELECT p.* FROM parcels p JOIN {self.index} r ON r.id = p.fid "
               "WHERE r.maxx >= ? AND r.minx <= ? AND r.maxy >= ? AND r.miny <= ?" + extra)
        with self.lock:
            return self.db.execute(sql, (x0, x1, y0, y1, *params)).fetchall()

    @staticmethod
    def ring(row: sqlite3.Row) -> list[list[float]]:
        return gpkg.read_gpkg_blob(row["geom"])[1][0]

    @staticmethod
    def _poly(ring: list[list[float]], origin: tuple[float, float]) -> Polygon:
        return Polygon(geometry.project(ring, origin))

    def feature(self, row: sqlite3.Row, **extra: Any) -> dict[str, Any]:
        return {"type": "Feature", "id": row["survey_no"],
                "geometry": {"type": "Polygon", "coordinates": [self.ring(row)]},
                "properties": {"surveyNo": row["survey_no"], "village": row["village"], "district": row["district"],
                               "status": row["status"], "areaSqm": row["area_sqm"], "boundaryAreaSqm": row["boundary_area_sqm"],
                               "perimeterM": row["perimeter_m"], "centroid": [row["centroid_lng"], row["centroid_lat"]],
                               "ownerInitials": row["owner_initials"], "owners": row["owners"], "transfers": row["transfers"],
                               "registeredAt": row["registered_at"], "lastTransferAt": row["last_transfer_at"], **extra}}

    @staticmethod
    def collection(features: list[dict[str, Any]], **meta: Any) -> dict[str, Any]:
        return {"type": "FeatureCollection", "features": features, **meta}

    # ------------------------------------------------------------------ queries

    def get(self, survey_no: str) -> sqlite3.Row | None:
        rows = self._rows("survey_no=?", (survey_no,))
        return rows[0] if rows else None

    def search(self, q: str = "", bbox: tuple[float, float, float, float] | None = None, status: str = "",
               min_area: float | None = None, max_area: float | None = None, village: str = "",
               limit: int = 500) -> list[dict[str, Any]]:
        clauses, params = [], []
        if q.strip():
            clauses.append("(p.survey_no LIKE ? OR p.village LIKE ? OR p.district LIKE ? OR p.owner_initials LIKE ?)")
            params += [f"%{q.strip()}%"] * 4
        if status:
            clauses.append("p.status = ?")
            params.append(status)
        if village:
            clauses.append("p.village = ? COLLATE NOCASE")
            params.append(village)
        if min_area is not None:
            clauses.append("p.area_sqm >= ?")
            params.append(min_area)
        if max_area is not None:
            clauses.append("p.area_sqm <= ?")
            params.append(max_area)
        extra = "".join(f" AND {c}" for c in clauses)
        if bbox:
            x0, y0, x1, y1 = bbox
            window = box(x0, y0, x1, y1)
            rows = [r for r in self._candidates(x0, y0, x1, y1, extra, tuple(params))
                    if Polygon(self.ring(r)).intersects(window)]
        else:
            with self.lock:
                rows = self.db.execute("SELECT p.* FROM parcels p WHERE 1" + extra, tuple(params)).fetchall()
        rows.sort(key=lambda r: r["survey_no"])
        return [self.feature(r) for r in rows[:limit]]

    def near(self, lng: float, lat: float, radius_m: float, limit: int = 20, exclude: str | None = None) -> list[dict[str, Any]]:
        m_lng, m_lat = geometry.metres_per_degree(lat)
        dx, dy = radius_m / m_lng, radius_m / m_lat
        origin = (lng, lat)
        here = Point(0, 0)
        out = []
        for r in self._candidates(lng - dx, lat - dy, lng + dx, lat + dy):
            if r["survey_no"] == exclude:
                continue
            d = self._poly(self.ring(r), origin).distance(here)
            if d <= radius_m:
                out.append((d, r))
        out.sort(key=lambda t: t[0])
        return [self.feature(r, distanceM=round(d, 1)) for d, r in out[:limit]]

    def at(self, lng: float, lat: float) -> dict[str, Any] | None:
        hits = [r for r in self._candidates(lng, lat, lng, lat) if Polygon(self.ring(r)).covers(Point(lng, lat))]
        return self.feature(hits[0]) if hits else None

    def _against(self, ring: list[list[float]], within_m: float, exclude: str | None) -> list[tuple[sqlite3.Row, Polygon, Polygon]]:
        x0, y0, x1, y1 = geometry.bbox(ring)
        cy = (y0 + y1) / 2
        m_lng, m_lat = geometry.metres_per_degree(cy)
        pad_x, pad_y = within_m / m_lng, within_m / m_lat
        origin = ((x0 + x1) / 2, cy)
        mine = self._poly(ring, origin)
        return [(r, mine, self._poly(self.ring(r), origin))
                for r in self._candidates(x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y) if r["survey_no"] != exclude]

    def neighbours(self, survey_no: str, within_m: float = 1.0) -> list[dict[str, Any]]:
        row = self.get(survey_no)
        if not row:
            return []
        out = []
        for r, mine, other in self._against(self.ring(row), within_m, survey_no):
            d = mine.distance(other)
            if d <= within_m:
                shared = mine.buffer(0.05).intersection(other.boundary).length if d < 0.05 else 0.0
                out.append(self.feature(r, distanceM=round(d, 2), sharedBoundaryM=round(shared, 1)))
        out.sort(key=lambda f: (f["properties"]["distanceM"], -f["properties"]["sharedBoundaryM"]))
        return out

    def overlaps(self, ring: list[list[float]], exclude: str | None = None, min_sqm: float = 0.5) -> list[dict[str, Any]]:
        """Parcels whose interiors overlap this boundary by at least `min_sqm` (sharing an edge is not an overlap)."""
        out = []
        for r, mine, other in self._against(ring, 0.0, exclude):
            if not mine.intersects(other):
                continue
            inter = mine.intersection(other).area
            if inter >= min_sqm:
                out.append(self.feature(r, overlapSqm=round(inter, 1),
                                        overlapPctOfThis=round(inter / mine.area * 100, 1) if mine.area else None))
        out.sort(key=lambda f: -f["properties"]["overlapSqm"])
        return out

    def check_boundary(self, boundary: dict[str, Any], area_sqm: float | None, exclude: str | None = None) -> dict[str, Any]:
        """What the Registrar sees before registering: is the boundary valid, does its area agree, what does it touch."""
        ring = boundary["coordinates"][0]
        shape = geometry.describe(boundary)
        dev = abs(shape["boundaryAreaSqm"] - area_sqm) / area_sqm * 100 if area_sqm else None
        over = self.overlaps(ring, exclude)
        x0, y0, x1, y1 = shape["bbox"]
        touching = []
        for r, mine, other in self._against(ring, 1.0, exclude):
            if mine.distance(other) <= 1.0 and not any(o["id"] == r["survey_no"] for o in over):
                touching.append(r["survey_no"])
        return {**shape, "areaDeviationPct": round(dev, 2) if dev is not None else None,
                "areaWithinTolerance": dev is None or dev <= 5.0, "overlaps": over, "touching": touching,
                "bboxWindow": [x0, y0, x1, y1]}

    def next_free_plot(self, village: str, area_sqm: float) -> dict[str, Any]:
        """The next free plot in the village layout that overlaps no parcel already in the database."""
        rings = [self.ring(r) for r in self._rows("village = ? COLLATE NOCASE", (village,))]
        for _ in range(20):
            plot = layout.next_free_plot(village, area_sqm, rings)
            if not self.overlaps(plot["coordinates"][0]):
                return plot
            rings.append(plot["coordinates"][0])        # taken by something drawn by hand: try the next slot
        raise RuntimeError(f"no free plot found in the {village} layout")

    def stats(self) -> dict[str, Any]:
        with self.lock:
            n = self.db.execute("SELECT COUNT(*), SUM(boundary_area_sqm), COUNT(DISTINCT village) FROM parcels").fetchone()
            by_village = self.db.execute("SELECT village, COUNT(*) AS n, SUM(boundary_area_sqm) AS area FROM parcels "
                                         "GROUP BY village ORDER BY n DESC").fetchall()
            by_status = self.db.execute("SELECT status, COUNT(*) FROM parcels GROUP BY status").fetchall()
            ext = self.db.execute("SELECT min_x, min_y, max_x, max_y, last_change FROM gpkg_contents WHERE table_name=?",
                                  (TABLE,)).fetchone()
            indexed = self.db.execute(f"SELECT COUNT(*) FROM {self.index}").fetchone()[0]
        return {"format": "OGC GeoPackage 1.4 (SQLite)", "file": self.path.name, "bytes": self.path.stat().st_size,
                "srs": "EPSG:4326 (WGS 84, longitude/latitude)", "table": TABLE,
                "spatialIndex": "SQLite R*Tree (gpkg_rtree_index)" if self.rtree else "bounding-box table (SQLite without R*Tree)",
                "indexedRows": indexed, "parcels": n[0], "totalAreaSqm": round(n[1] or 0, 1), "villages": n[2],
                "byVillage": [{"village": r["village"], "parcels": r["n"], "areaSqm": round(r["area"] or 0, 1)} for r in by_village],
                "byStatus": {r[0]: r[1] for r in by_status},
                "extent": None if ext is None or ext[0] is None else
                {"minLng": ext[0], "minLat": ext[1], "maxLng": ext[2], "maxLat": ext[3]},
                "lastChange": ext[4] if ext else None}

    def all_features(self) -> list[dict[str, Any]]:
        return [self.feature(r) for r in self._rows()]


def bbox_param(text: str | None) -> tuple[float, float, float, float] | None:
    if not text:
        return None
    parts = [float(x) for x in text.split(",")]
    if len(parts) != 4 or not all(math.isfinite(x) for x in parts):
        raise ValueError("bbox must be minLng,minLat,maxLng,maxLat")
    x0, y0, x1, y1 = parts
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"))
