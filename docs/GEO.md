# The geospatial database, the chain of ownership and signed transfer records

## Boundaries on the ledger

`RegisterParcel(surveyNo, village, district, areaSqm, ownerName, ownerRef, boundaryGeoJSON)` takes the parcel's surveyed boundary as a GeoJSON Polygon in `[longitude, latitude]` (WGS 84). `chaincode/landrec/geometry.py` (pure Python, deterministic, so every peer agrees) validates it (one ring, 3 to 64 corners, inside India, edges that do not cross), normalises it (closed, counter-clockwise, 7 decimals) and measures it in a local tangent plane. The ledger refuses a boundary whose area differs from `areaSqm` by more than 5%, and stores `boundary`, `boundaryAreaSqm`, `perimeterM`, `centroid`, `bbox`, `corners` on the parcel.

The demo has no survey department, so the Registrar can place a parcel in the **village layout** (`geo/layout.py`): rows of 40 m deep plots, two rows back to back then a 10 m road, rotated to each village's bearing, around real places near Hanamkonda. The next free slot is worked out from the boundaries already registered, so it survives a rebuild. Or paste the surveyor's corners.

## The geospatial database (`geo/`)

An **OGC GeoPackage 1.4** file, `data/geo/landchain.gpkg`: SQLite with the standard metadata tables (`gpkg_spatial_ref_sys`, `gpkg_contents`, `gpkg_geometry_columns`, `gpkg_extensions`), a `parcels` feature table with GeoPackage binary geometry (header + WKB, written in `geo/gpkg.py`), and an **R*Tree** spatial index registered as `gpkg_rtree_index`. QGIS, ArcGIS and GDAL (`ogrinfo landchain.gpkg`) open it directly and use the index; `/api/public/geo/export.gpkg` downloads a consistent copy.

Queries run in two steps, the way spatial databases do: the R*Tree finds candidates whose bounding boxes qualify, then Shapely tests the exact geometry in metres.

| Endpoint (public) | Question |
|---|---|
| `GET /api/public/geo/parcels?bbox=&q=&status=&village=&minArea=&maxArea=` | parcels in the map window, filtered (GeoJSON) |
| `GET /api/public/geo/near?lng=&lat=&radius=` | parcels within a radius, nearest first, with distances |
| `GET /api/public/geo/at?lng=&lat=` | the parcel under a point |
| `GET /api/public/geo/parcel?surveyNo=&within=` | one parcel and its neighbours (shared boundary length) |
| `GET /api/public/geo/overlaps?surveyNo=` | parcels whose boundaries overlap this one |
| `GET /api/public/geo/stats`, `export.gpkg`, `export.geojson` | the database itself |
| `POST /api/geo/check-boundary`, `GET /api/geo/next-plot` (officers) | check a proposed boundary; the next free plot |

**The ledger is the source of truth; the GeoPackage is an index.** The gateway (`gateway/geo_index.py`) reads every parcel at start-up and then follows the peer's block events, rewriting a parcel's row whenever a transaction touches it. `./landchain.sh geo-rebuild` deletes the file and rebuilds it from the ledger. A ledger cannot answer "which parcels overlap this one?" without scanning everything; that is why registration checks overlaps against the GeoPackage, refuses an overlapping boundary unless the Registrar records a dispute, and why the validator's `boundary_overlap` check and the agent's `parcel_boundary_overlaps` tool ask it.

## The chain of ownership

`OwnershipChain(surveyNo)` (chaincode) walks every committed version of the parcel and returns one link per owner: who, how they acquired it (first registration or an approved transfer, with its application, consideration and the Collector), from which transaction until which. The gateway adds block numbers (`/txs/{id}` on the peer) and, for the public, shows owners as initials (`/api/public/ownership`); officers get full names (`/api/parcel`).

## Signed transfer records (`gateway/records.py`)

Every transfer file, approved, rejected or in progress, has:

| Copy | Who | Contents |
|---|---|---|
| public | anyone: `GET /api/public/records/{appId}` | parcel and boundary corners, status, every step with transaction and block, document and validator-report fingerprints, consent fingerprints, the chain of ownership; people as initials, no amounts, no document IDs |
| party | the seller or the buyer, signed in: `GET /api/citizen/records/{appId}` | issued to that person; adds full names, DigiLocker IDs, both DIDs and consent signatures, the consideration, document IDs, officers' names, the survey, the validator's explanation for citizens, and the recipient's DigiLocker access log for this file |

Each is **one text file with its OpenPGP signature inside** (a cleartext-signed message, RFC 4880 §7, written in `agent/pgp.py`), ending with the same content as canonical JSON. It is signed by the **Records Office** key (`data/gateway/keys/`), which the Collector attests on the ledger (`RegisterRecordsKey`; `./landchain.sh attest-records`, done by setup). Check one:

```bash
curl -s localhost:13900/api/public/records-key | python3 -c 'import sys,json;print(json.load(sys.stdin)["publicKey"])' > records.asc
gpg --import records.asc && gpg --verify landchain-APP-...-public.asc
```

or drop it on `/verify → A transfer record`, which checks the signature with OpenPGP.js, that the signer is the attested key, and that the file exists on the ledger.
