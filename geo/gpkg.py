"""
The OGC GeoPackage encoding, written by hand so it can be read and changed.

A GeoPackage (https://www.geopackage.org, OGC 12-128r19) is an SQLite file
with a few required metadata tables and geometry stored as a "GeoPackage
binary" blob: a small header followed by standard Well-Known Binary (WKB).
QGIS, ArcGIS, GDAL/ogr2ogr and most GIS tools open it directly.

Spatial indexing uses SQLite's built-in R*Tree module, registered with the
`gpkg_rtree_index` extension exactly as the standard names it, so GIS tools
use the same index this project does.
"""

from __future__ import annotations

import sqlite3
import struct
from typing import Any

GPKG_APPLICATION_ID = 0x47504B47      # "GPKG"
GPKG_USER_VERSION = 10400             # GeoPackage 1.4
SRS_WGS84 = 4326

WKB_POLYGON = 3


# ---------------------------------------------------------------------------
# Well-Known Binary
# ---------------------------------------------------------------------------

def wkb_polygon(rings: list[list[list[float]]]) -> bytes:
    """Little-endian WKB for a Polygon: byte order, type, ring count, then each ring's points (x=lng, y=lat)."""
    out = struct.pack("<BII", 1, WKB_POLYGON, len(rings))
    for ring in rings:
        out += struct.pack("<I", len(ring))
        for x, y in ring:
            out += struct.pack("<dd", x, y)
    return out


def read_wkb_polygon(data: bytes) -> list[list[list[float]]]:
    order = "<" if data[0] == 1 else ">"
    gtype, nrings = struct.unpack(order + "II", data[1:9])
    if gtype % 1000 != WKB_POLYGON:
        raise ValueError(f"not a polygon (WKB type {gtype})")
    pos, rings = 9, []
    for _ in range(nrings):
        (n,) = struct.unpack(order + "I", data[pos:pos + 4])
        pos += 4
        ring = []
        for _ in range(n):
            x, y = struct.unpack(order + "dd", data[pos:pos + 16])
            ring.append([x, y])
            pos += 16
        rings.append(ring)
    return rings


# ---------------------------------------------------------------------------
# GeoPackage binary geometry (header + WKB)
# ---------------------------------------------------------------------------

def gpkg_blob(rings: list[list[list[float]]], srs_id: int = SRS_WGS84) -> bytes:
    """'GP', version 0, flags (little-endian, envelope [minx, maxx, miny, maxy]), srs id, envelope, WKB."""
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    flags = 0b0000_0011           # bit 0: little-endian header; bits 1-3 = 1: envelope is minx,maxx,miny,maxy
    header = b"GP" + bytes([0, flags]) + struct.pack("<i", srs_id) + struct.pack("<dddd", min(xs), max(xs), min(ys), max(ys))
    return header + wkb_polygon(rings)


def read_gpkg_blob(blob: bytes) -> tuple[int, list[list[list[float]]]]:
    if blob[:2] != b"GP":
        raise ValueError("not a GeoPackage geometry blob")
    flags = blob[3]
    order = "<" if flags & 1 else ">"
    (srs_id,) = struct.unpack(order + "i", blob[4:8])
    env = (flags >> 1) & 0b111
    env_len = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[env]
    return srs_id, read_wkb_polygon(blob[8 + env_len:])


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def create_schema(db: sqlite3.Connection, table: str, columns_sql: str, description: str) -> bool:
    """Create the GeoPackage metadata tables, one feature table and its R*Tree index. Returns whether R*Tree is on."""
    db.execute(f"PRAGMA application_id = {GPKG_APPLICATION_ID}")
    db.execute(f"PRAGMA user_version = {GPKG_USER_VERSION}")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY, organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL, description TEXT);
        CREATE TABLE IF NOT EXISTS gpkg_contents (
            table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT UNIQUE, description TEXT DEFAULT '',
            last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER,
            CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
        CREATE TABLE IF NOT EXISTS gpkg_geometry_columns (
            table_name TEXT NOT NULL, column_name TEXT NOT NULL, geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name));
        CREATE TABLE IF NOT EXISTS gpkg_extensions (
            table_name TEXT, column_name TEXT, extension_name TEXT NOT NULL, definition TEXT NOT NULL, scope TEXT NOT NULL,
            CONSTRAINT ge_tce UNIQUE (table_name, column_name, extension_name));
    """)
    srs = [
        ("Undefined cartesian SRS", -1, "NONE", -1, "undefined", "undefined cartesian coordinate reference system"),
        ("Undefined geographic SRS", 0, "NONE", 0, "undefined", "undefined geographic coordinate reference system"),
        ("WGS 84 geodetic", 4326, "EPSG", 4326,
         'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
         'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,'
         'AUTHORITY["EPSG","9122"]],AXIS["Latitude",NORTH],AXIS["Longitude",EAST],AUTHORITY["EPSG","4326"]]',
         "longitude/latitude coordinates in decimal degrees on the WGS 84 spheroid"),
    ]
    db.executemany("INSERT OR IGNORE INTO gpkg_spatial_ref_sys VALUES (?,?,?,?,?,?)", srs)
    db.execute(f"CREATE TABLE IF NOT EXISTS {table} (fid INTEGER PRIMARY KEY AUTOINCREMENT, geom BLOB, {columns_sql})")
    db.execute("INSERT OR IGNORE INTO gpkg_contents (table_name, data_type, identifier, description, srs_id) "
               "VALUES (?, 'features', ?, ?, ?)", (table, table, description, SRS_WGS84))
    db.execute("INSERT OR IGNORE INTO gpkg_geometry_columns VALUES (?, 'geom', 'POLYGON', ?, 0, 0)", (table, SRS_WGS84))
    try:
        db.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS rtree_{table}_geom USING rtree(id, minx, maxx, miny, maxy)")
        db.execute("INSERT OR IGNORE INTO gpkg_extensions VALUES (?, 'geom', 'gpkg_rtree_index', "
                   "'http://www.geopackage.org/spec120/#extension_rtree', 'write-only')", (table,))
        rtree = True
    except sqlite3.OperationalError:          # an SQLite built without R*Tree: fall back to a plain bbox table
        db.execute(f"CREATE TABLE IF NOT EXISTS bbox_{table}_geom (id INTEGER PRIMARY KEY, minx REAL, maxx REAL, "
                   "miny REAL, maxy REAL)")
        db.execute(f"CREATE INDEX IF NOT EXISTS bbox_{table}_x ON bbox_{table}_geom (minx, maxx)")
        rtree = False
    db.commit()
    return rtree


def update_extent(db: sqlite3.Connection, table: str) -> dict[str, Any] | None:
    row = db.execute(f"SELECT MIN(minx), MIN(miny), MAX(maxx), MAX(maxy) FROM {index_table(db, table)}").fetchone()
    db.execute("UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=?, "
               "last_change=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE table_name=?", (*row, table))
    return None if row[0] is None else {"minLng": row[0], "minLat": row[1], "maxLng": row[2], "maxLat": row[3]}


def index_table(db: sqlite3.Connection, table: str) -> str:
    name = f"rtree_{table}_geom"
    found = db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()
    return name if found else f"bbox_{table}_geom"
