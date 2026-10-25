"""Parcel boundaries on the ledger, the ownership chain, and the geospatial database."""

import json

import pytest

from chaincode.landrec import geometry
from geo import gpkg, layout
from geo.store import GeoStore

from .harness import Harness
from .test_chaincode import Citizen, open_transfer


def square(lng=79.50, lat=17.97, side_m=50.0):
    m_lng, m_lat = geometry.metres_per_degree(lat)
    dx, dy = side_m / m_lng, side_m / m_lat
    return {"type": "Polygon", "coordinates": [[[lng, lat], [lng + dx, lat], [lng + dx, lat + dy], [lng, lat + dy], [lng, lat]]]}


def test_geometry_area_and_validation():
    b = geometry.parse_boundary(square(side_m=50))
    assert abs(geometry.area_sqm(b["coordinates"][0]) - 2500) < 1
    assert geometry.signed_area(geometry.project(b["coordinates"][0])) > 0          # counter-clockwise (RFC 7946)
    cw = {"type": "Polygon", "coordinates": [list(reversed(square()["coordinates"][0]))]}
    assert geometry.parse_boundary(cw) == geometry.parse_boundary(square())       # normalised
    bowtie = {"type": "Polygon", "coordinates": [[[79.5, 17.97], [79.501, 17.971], [79.501, 17.97], [79.5, 17.971], [79.5, 17.97]]]}
    with pytest.raises(ValueError, match="cross"):
        geometry.parse_boundary(bowtie)
    with pytest.raises(ValueError, match="outside India"):
        geometry.parse_boundary({"type": "Polygon", "coordinates": [[[17.97, 79.5], [17.98, 79.5], [17.98, 79.51], [17.97, 79.5]]]})
    with pytest.raises(ValueError, match="Polygon"):
        geometry.parse_boundary({"type": "Point", "coordinates": [79.5, 17.97]})


def test_ledger_checks_boundary_area():
    h = Harness()
    owner = Citizen("ref-a")
    h.call("Org1MSP", "portal", "RegisterCitizen", owner.ref, owner.did)
    p = h.call("Org1MSP", "registrar", "RegisterParcel", "SY-G1", "Kazipet", "Hanamkonda", "2500", "Ramesh Kumar",
               owner.ref, json.dumps(square(side_m=50)))
    assert p["boundary"]["type"] == "Polygon" and abs(p["boundaryAreaSqm"] - 2500) < 1 and p["corners"] == 4
    with pytest.raises(Exception, match="tolerance is 5%"):
        h.call("Org1MSP", "registrar", "RegisterParcel", "SY-G2", "Kazipet", "Hanamkonda", "4000", "Ramesh Kumar",
               owner.ref, json.dumps(square(side_m=50)))
    with pytest.raises(Exception, match="boundary is invalid"):
        h.call("Org1MSP", "registrar", "RegisterParcel", "SY-G3", "Kazipet", "Hanamkonda", "4000", "Ramesh Kumar",
               owner.ref, '{"type": "Polygon"}')
    legacy = h.call("Org1MSP", "registrar", "RegisterParcel", "SY-G4", "Kazipet", "Hanamkonda", "900", "Ramesh Kumar", owner.ref)
    assert legacy["boundary"] is None


def test_ownership_chain_follows_transfers():
    h = Harness()
    a, b, c = Citizen("ref-a"), Citizen("ref-b"), Citizen("ref-c")
    for x in (a, b, c):
        h.call("Org1MSP", "portal", "RegisterCitizen", x.ref, x.did)
    h.call("Org1MSP", "registrar", "RegisterParcel", "SY-1", "Kazipet", "Hanamkonda", "2500", "Asha Rao", a.ref,
           json.dumps(square()))

    def transfer(app, seller, buyer, amount):
        open_transfer(h, seller, buyer, app=app, amount=amount)
        h.call("Org1MSP", "clerk", "AnchorDocument", app, (app[-1] * 64)[:64].replace(app[-1], "a"), f"DOC-{app}", "SALEDEED", "SRO")
        h.call("Org1MSP", "registrar", "ForwardToRevenue", app)
        h.call("Org2MSP", "verifier", "VerifyDocuments", app, json.dumps(["a" * 64]))
        h.call("Org2MSP", "surveyor", "RecordSurvey", app, "2500", "N: road")
        h.call("Org2MSP", "verifier", "ForwardToCollectorate", app)
        h.call("Org3MSP", "collector", "ApproveTransfer", app, "ok")

    transfer("APP-1", a, b, 4500000)
    # a rejected file in between leaves the chain alone
    open_transfer(h, b, c, app="APP-2", amount=100)
    h.call("Org3MSP", "collector", "RejectApplication", "APP-2", "price too low")
    chain = h.call("Org3MSP", "collector", "OwnershipChain", "SY-1")
    assert chain["owners"] == 2
    first, second = chain["links"]
    assert first["acquired"] == "registration" and first["ownerRef"] == a.ref and first["until"]
    assert second["acquired"] == "transfer" and second["appId"] == "APP-1" and second["considerationINR"] == 4500000
    assert second["fromName"] == "Asha Rao" and second["until"] is None and second["decidedBy"]


def test_records_key_is_attested_by_the_collector():
    h = Harness()
    arm = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nxx\n-----END PGP PUBLIC KEY BLOCK-----\n"
    with pytest.raises(Exception, match="ACCESS DENIED"):
        h.call("Org1MSP", "registrar", "RegisterRecordsKey", "A" * 40, arm)
    rec = h.call("Org3MSP", "collector", "RegisterRecordsKey", "A" * 40, arm)
    assert rec["status"] == "ACTIVE" and rec["attestedBy"]
    rot = h.call("Org3MSP", "collector", "RegisterRecordsKey", "B" * 40, arm)
    assert rot["previousKeys"][0]["pgpFingerprint"] == "A" * 40
    assert h.call("Org1MSP", "registrar", "ReadRecordsKey")["pgpFingerprint"] == "B" * 40


def test_geopackage_blob_roundtrip():
    ring = geometry.parse_boundary(square())["coordinates"]
    blob = gpkg.gpkg_blob(ring)
    assert blob[:2] == b"GP" and gpkg.read_gpkg_blob(blob) == (4326, ring)


def test_geo_store_queries(tmp_path):
    s = GeoStore(tmp_path / "t.gpkg")
    for i, area in enumerate([2400, 1450, 900, 2400]):
        b = s.next_free_plot("Kazipet", area)
        assert s.overlaps(b["coordinates"][0]) == []                   # the layout never overlaps a parcel
        s.upsert({"surveyNo": f"SY-{i}", "village": "Kazipet", "district": "Hanamkonda", "status": "ACTIVE",
                  "areaSqm": area, "ownerName": "Ramesh Kumar", "boundary": b})
    assert s.rtree and s.stats()["parcels"] == 4 and s.stats()["indexedRows"] == 4
    names = [f["id"] for f in s.neighbours("SY-1")]
    assert "SY-0" in names and "SY-2" in names                        # shares both side boundaries
    shared = {f["id"]: f["properties"]["sharedBoundaryM"] for f in s.neighbours("SY-1")}
    assert abs(shared["SY-0"] - layout.PLOT_DEPTH_M) < 1
    ring = s.ring(s.get("SY-2"))
    c = geometry.centroid(ring)
    assert s.at(*c)["id"] == "SY-2"
    assert [f["id"] for f in s.near(*c, 5)] == ["SY-2"]
    overlap = s.check_boundary({"type": "Polygon", "coordinates": [ring]}, 900)
    assert overlap["overlaps"][0]["id"] == "SY-2" and overlap["overlaps"][0]["properties"]["overlapSqm"] > 890
    assert [f["id"] for f in s.search(bbox=(c[0] - 1e-5, c[1] - 1e-5, c[0] + 1e-5, c[1] + 1e-5))] == ["SY-2"]
    assert len(s.search(min_area=2000)) == 2 and len(s.search(q="SY-3")) == 1
