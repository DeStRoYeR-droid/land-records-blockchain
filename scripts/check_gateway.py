#!/usr/bin/env python3
"""
check_gateway.py -- drive one complete transfer through the gateway's REST API,
exactly the way the three dashboards do, and check every answer.

    ./landchain.sh check            (network + gateway must be up)

Two citizens register with fresh did:key identities (the browser's job, done
here with the same Ed25519 maths), sign a transfer intent, and the five
officers take it through Registration -> validator -> Revenue -> Collectorate.
Then it downloads the agent's signed report and verifies the OpenPGP signature
against the key fingerprint the ledger attests.
"""

from __future__ import annotations

import base64
import json
import secrets
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from agent.pgp import verify_cleartext, verify_detached  # noqa: E402
from chaincode.landrec import didkey  # noqa: E402
from chaincode.landrec.landrec import canonical  # noqa: E402
from nodes.common import config  # noqa: E402
from nodes.common.canonical import sha256_hex  # noqa: E402

GW = config.url("gateway")
OTP = "123456"
TTY = sys.stdout.isatty()
G, R, D, X = ("\033[32m", "\033[31m", "\033[2m", "\033[0m") if TTY else ("", "", "", "")
http = httpx.Client(base_url=GW, timeout=60)
passed = 0


def ok(label: str, detail: str = "") -> None:
    global passed
    passed += 1
    print(f"  {G}PASS{X} {label}" + (f"  {D}{detail}{X}" if detail else ""))


def fail(label: str, detail: str) -> None:
    print(f"  {R}FAIL{X} {label}: {detail}")
    sys.exit(1)


def call(method: str, path: str, token: str | None = None, expect: int = 200, raw: bool = False, **kw):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = http.request(method, path, headers=headers, **kw)
    if resp.status_code != expect:
        fail(f"{method} {path}", f"expected HTTP {expect}, got {resp.status_code}: {resp.text[:300]}")
    return resp if raw or "json" not in resp.headers.get("content-type", "") else resp.json()


class Browser:
    """What the citizen dashboard does in the browser: keep a did:key, sign."""

    def __init__(self, name: str, mobile: str, last4: str):
        self.name, self.mobile, self.last4 = name, mobile, last4
        self.key = Ed25519PrivateKey.generate()
        raw = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.did = didkey.did_from_public_key(raw)
        self.token = ""
        self.dl_id = ""

    def sign(self, obj: dict) -> str:
        return base64.b64encode(self.key.sign(canonical(obj))).decode()

    def register(self) -> None:
        ch = call("POST", "/api/citizen/challenge", json={"purpose": "register", "did": self.did})["challenge"]
        proof = self.sign({"type": "landchain.register/v1", "did": self.did, "challenge": ch})
        res = call("POST", "/api/citizen/register", json={"name": self.name, "mobile": self.mobile,
                                                          "aadhaarLast4": self.last4, "did": self.did,
                                                          "challenge": ch, "signature": proof})
        self.dl_id, self.token = res["citizen"]["digilockerId"], res["token"]

    def login(self) -> None:
        ch = call("POST", "/api/citizen/challenge", json={"purpose": "login", "digilockerId": self.dl_id})["challenge"]
        proof = self.sign({"type": "landchain.login/v1", "digilockerId": self.dl_id, "challenge": ch})
        self.token = call("POST", "/api/citizen/login", json={"digilockerId": self.dl_id, "otp": OTP,
                                                               "challenge": ch, "signature": proof})["token"]


def officer(username: str) -> str:
    return call("POST", "/api/auth/officer", json={"username": username, "password": username})["token"]


def main() -> None:
    print(f"Gateway at {GW}\n")
    tag = uuid.uuid4().hex[:4].upper()

    # -- public ---------------------------------------------------------------
    call("GET", "/health")
    summary = call("GET", "/api/public/summary")
    ok("public summary", f"{summary['parcels']} parcels, {summary['applications']} applications, "
                         f"block {summary['height']}, agent online={summary['agent']['online']}")
    net = call("GET", "/api/network")
    down = [n["name"] for n in net["nodes"] if not n["up"]]
    if down:
        fail("network", f"down: {down}")
    ok("network view", f"leader {net['leader']}, height {net['height']}, {len(net['nodes'])} nodes up")

    # -- officers ---------------------------------------------------------------
    tokens = {u: officer(u) for u in ("registrar1", "clerk1", "verifier1", "surveyor1", "collector1")}
    ok("five officers signed in", ", ".join(tokens))
    call("POST", "/api/auth/officer", json={"username": "clerk1", "password": "wrong"}, expect=401)
    call("POST", "/api/auth/officer", json={"username": "agent1", "password": "agent1"}, expect=401)
    ok("wrong password and the agent's service identity are refused")
    call("GET", "/api/officer/summary", expect=401)
    ok("no session -> 401")

    # -- citizens -----------------------------------------------------------------
    seller = Browser(f"Sunitha Reddy {tag}", f"98{secrets.randbelow(10**8):08d}", "4821")
    buyer = Browser(f"Arjun Rao {tag}", f"99{secrets.randbelow(10**8):08d}", "7710")
    seller.register()
    buyer.register()
    ok("two citizens registered", f"{seller.dl_id} / {buyer.dl_id}; DIDs bound on the ledger")
    impostor = Browser("Impostor Person", "9000000000", "1111")
    ch = call("POST", "/api/citizen/challenge", json={"purpose": "login", "digilockerId": seller.dl_id})["challenge"]
    forged = impostor.sign({"type": "landchain.login/v1", "digilockerId": seller.dl_id, "challenge": ch})
    call("POST", "/api/citizen/login", json={"digilockerId": seller.dl_id, "otp": OTP, "challenge": ch,
                                             "signature": forged}, expect=401)
    ok("sign-in with the right OTP but the wrong key is refused")
    seller.login()
    buyer.login()
    me = call("GET", "/api/citizen/me", seller.token)
    if me["profile"]["onChain"]["did"] != seller.did:
        fail("citizen profile", "on-chain DID differs")
    ok("citizens signed in with OTP + DID challenge", f"DID {seller.did[:24]}...")

    # -- Sub-Registrar: parcel and documents -----------------------------------------
    survey_no = f"SY-{tag}-12/4A"
    area = 1450
    call("POST", "/api/parcels", tokens["registrar1"], json={"surveyNo": survey_no, "village": "Kazipet",
                                                            "district": "Hanamkonda", "areaSqm": area,
                                                            "ownerDigilockerId": seller.dl_id})
    call("POST", "/api/parcels", tokens["clerk1"], expect=403,
         json={"surveyNo": f"{survey_no}X", "village": "Kazipet", "district": "Hanamkonda", "areaSqm": 1,
               "ownerDigilockerId": seller.dl_id})
    ok("registrar registered the parcel; the clerk cannot", survey_no)

    # -- the geospatial database ---------------------------------------------------------------
    geo = call("GET", f"/api/public/geo/parcel?surveyNo={survey_no}")
    feat = geo["parcel"]
    ring = feat["geometry"]["coordinates"][0]
    if feat["geometry"]["type"] != "Polygon" or abs(feat["properties"]["boundaryAreaSqm"] - area) / area > 0.05:
        fail("boundary", json.dumps(feat["properties"])[:200])
    ok("the parcel got a surveyed boundary and is in the geospatial database",
       f"{feat['properties']['boundaryAreaSqm']} sq.m, centroid {feat['properties']['centroid']}")
    lng, lat = feat["properties"]["centroid"]
    at = call("GET", f"/api/public/geo/at?lng={lng}&lat={lat}")["parcel"]
    near = call("GET", f"/api/public/geo/near?lng={lng}&lat={lat}&radius=300")["features"]
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    window = call("GET", f"/api/public/geo/parcels?bbox={min(xs)},{min(ys)},{max(xs)},{max(ys)}")["features"]
    if not at or at["id"] != survey_no or near[0]["id"] != survey_no or survey_no not in [f["id"] for f in window]:
        fail("spatial queries", f"at={at and at['id']} near={[f['id'] for f in near[:3]]}")
    ok("spatial queries: the parcel under a point, nearest first, inside a map window",
       f"{len(near)} within 300 m, {len(window)} in the window, neighbours {len(geo['neighbours']['features'])}")
    shifted = {"type": "Polygon", "coordinates": [[[x + (ring[1][0] - ring[0][0]) * 0.3, y + (ring[1][1] - ring[0][1]) * 0.3]
                                                   for x, y in ring]]}
    pre = call("POST", "/api/geo/check-boundary", tokens["registrar1"], json={"boundary": shifted, "areaSqm": area})
    refused = call("POST", "/api/parcels", tokens["registrar1"], expect=409, json={
        "surveyNo": f"{survey_no}-DUP", "village": "Kazipet", "district": "Hanamkonda", "areaSqm": area,
        "ownerDigilockerId": seller.dl_id, "boundary": shifted})
    if not pre["overlaps"] or pre["overlaps"][0]["id"] != survey_no:
        fail("overlap pre-check", json.dumps(pre)[:200])
    ok("a boundary over another parcel's ground is caught before registering", refused["error"][:70])
    stats = call("GET", "/api/public/geo/stats")
    gpkg = call("GET", "/api/public/geo/export.gpkg", raw=True).content
    if b"SQLite format 3" not in gpkg[:16] or gpkg[68:72] != b"GPKG" or "R*Tree" not in stats["spatialIndex"]:
        fail("GeoPackage export", f"{gpkg[:16]!r} {gpkg[68:72]!r} {stats['spatialIndex']}")
    ok("GeoPackage export (application id GPKG, R*Tree index)", f"{stats['parcels']} parcels, {len(gpkg)} bytes")
    deed = call("POST", "/api/documents/issue", tokens["registrar1"], json={
        "docType": "SALEDEED", "digilockerId": seller.dl_id, "surveyNo": survey_no, "village": "Kazipet",
        "district": "Hanamkonda", "extentSqm": area, "vendor": seller.name, "purchaser": buyer.name,
        "considerationINR": 3850000})
    ec = call("POST", "/api/documents/issue", tokens["registrar1"], json={
        "docType": "ENCUMBRANCE", "digilockerId": seller.dl_id, "surveyNo": survey_no})
    docs = call("GET", "/api/citizen/documents", seller.token)["documents"]
    if {deed["docId"], ec["docId"]} - {d["docId"] for d in docs}:
        fail("documents", f"seller's DigiLocker does not show the issued documents: {docs}")
    ok("deed + EC issued into the seller's DigiLocker", f"{len(docs)} documents")

    # -- the transfer intent: seller signs, buyer counter-signs ---------------------------
    prep = call("POST", "/api/citizen/intents/prepare", seller.token,
                json={"surveyNo": survey_no, "buyerDigilockerId": buyer.dl_id, "considerationINR": "38,50,000"})
    intent = prep["intent"]
    if prep["canonical"].encode() != canonical(intent):
        fail("intent canonical form", "server and client disagree")
    call("POST", "/api/citizen/intents/prepare", buyer.token, expect=403,
         json={"surveyNo": survey_no, "buyerDigilockerId": seller.dl_id, "considerationINR": "1"})
    rec = call("POST", "/api/citizen/intents", seller.token, json={"intent": intent, "signature": seller.sign(intent)})
    call("POST", f"/api/citizen/intents/{intent['intentId']}/accept", buyer.token, expect=400,
         json={"signature": impostor.sign(intent)})
    call("POST", f"/api/citizen/intents/{intent['intentId']}/accept", buyer.token,
         json={"signature": buyer.sign(intent)})
    ok("intent signed by seller and buyer (forged acceptance refused)", f"intent {rec['id'][:12]}...")

    # -- Registration clerk ------------------------------------------------------------
    reqs = call("GET", "/api/transfer-requests", tokens["clerk1"])["intents"]
    if not any(i["id"] == intent["intentId"] and i["status"] == "ready" for i in reqs):
        fail("transfer requests", "the ready intent is not in the clerk's inbox")
    opened = call("POST", "/api/applications", tokens["clerk1"], json={"intentId": intent["intentId"]})
    app_id = opened["appId"]
    ok("clerk opened the transfer file", f"{app_id} in block {opened['block']}, endorsed by {len(opened['endorsers'])}")
    call("POST", "/api/applications", tokens["clerk1"], json={"intentId": intent["intentId"]}, expect=409)
    ok("the same intent cannot be filed twice")
    sdocs = call("GET", f"/api/applications/{app_id}/seller-documents", tokens["clerk1"])["documents"]
    for d in sdocs:
        if d["docType"] in ("SALEDEED", "ENCUMBRANCE") and survey_no in d["description"]:
            call("POST", f"/api/applications/{app_id}/anchor", tokens["clerk1"], json={"docId": d["docId"], "otp": OTP})
    a = call("GET", f"/api/applications/{app_id}", tokens["clerk1"])["application"]
    ok("documents anchored with the seller's OTP", ", ".join(d["documentType"] for d in a["documents"]))

    # -- Sub-Registrar hands over: the validator wakes up -----------------------------------
    t0 = time.time()
    call("POST", f"/api/applications/{app_id}/forward", tokens["registrar1"])
    run = None
    while time.time() - t0 < 90:
        runs = call("GET", f"/api/agent/runs?appId={app_id}", tokens["verifier1"])["runs"]
        done = [r for r in runs if r["status"] in ("anchored", "failed")]
        if done:
            run = done[0]
            break
        time.sleep(1)
    if not run or run["status"] != "anchored":
        fail("validator", f"no anchored report within 90 s: {run}")
    ok("validator ran on the handoff, signed and anchored its report",
       f"{run['verdict']['status']} ({run['verdict']['passed']} pass, {run['verdict']['attention']} attention, "
       f"{run['verdict']['skipped']} skipped) "
       f"in {time.time() - t0:.1f}s, block {run['anchor']['block']}")

    # -- the citizen verifies the agent's signature themselves -----------------------------
    reps = call("GET", f"/api/citizen/reports?appId={app_id}", buyer.token)["reports"]
    if not reps:
        fail("citizen reports", "the buyer cannot see the report")
    rid = reps[0]["runId"]
    body = call("GET", f"/api/agent/runs/{rid}/report.json", buyer.token, raw=True).content
    sig = call("GET", f"/api/agent/runs/{rid}/report.json.asc", buyer.token, raw=True).text
    keyinfo = call("GET", "/api/public/agent-key")
    v = verify_detached(keyinfo["publicKey"], body, sig)
    if not v.valid or v.fingerprint != keyinfo["onChain"]["pgpFingerprint"]:
        fail("PGP verification", f"{v}")
    anchored = call("GET", f"/api/public/verify/report/{sha256_hex(body)}")
    if not anchored.get("anchored", anchored.get("valid")):
        fail("report anchor", json.dumps(anchored)[:300])
    tampered = verify_detached(keyinfo["publicKey"], body.replace(b'"clear"', b'"CLEAR"', 1) if b'"clear"' in body
                               else body + b" ", sig)
    if tampered.valid:
        fail("PGP verification", "a tampered report verified")
    ok("buyer downloaded report + signature: VALID, fingerprint = on-chain key, hash anchored; tampered copy BAD",
       v.fingerprint)
    stranger = Browser(f"Stranger {tag}", "9111111111", "2222")
    stranger.register()
    call("GET", f"/api/agent/runs/{rid}/report.json", stranger.token, expect=403)
    call("GET", f"/api/citizen/reports?appId={app_id}", stranger.token, expect=403)
    ok("a citizen who is not a party cannot read the report")

    # -- Revenue and Collectorate ---------------------------------------------------------
    call("POST", f"/api/applications/{app_id}/approve", tokens["clerk1"], json={}, expect=403)
    ok("a clerk cannot approve")
    chk = call("POST", f"/api/applications/{app_id}/verify", tokens["verifier1"], json={"otp": OTP})
    if not all(c["match"] for c in chk["checked"]):
        fail("verify", json.dumps(chk["checked"]))
    call("POST", f"/api/applications/{app_id}/survey", tokens["surveyor1"],
         json={"measuredAreaSqm": area - 6, "boundaries": "N: Road | S: Canal | E: Sy 12/5 | W: Sy 12/3"})
    call("POST", f"/api/applications/{app_id}/forward-collectorate", tokens["verifier1"])
    ok("Revenue re-verified (own consent, own hashing), surveyed and forwarded")
    call("POST", f"/api/applications/{app_id}/approve", tokens["collector1"], json={"remarks": "Approved via check_gateway"})
    me_b = call("GET", "/api/citizen/me", buyer.token)
    if not any(p["surveyNo"] == survey_no for p in me_b["parcels"]):
        fail("ownership", "the buyer does not own the parcel after approval")
    ok("collector approved; the parcel now shows on the buyer's dashboard")

    # -- chain of ownership and the signed transfer records --------------------------------------
    chain = call("GET", f"/api/public/ownership?surveyNo={survey_no}")
    if chain["owners"] != 2 or chain["links"][1]["appId"] != app_id or chain["links"][1]["owner"] == buyer.name:
        fail("ownership chain", json.dumps(chain)[:300])
    ok("chain of ownership: registered owner, then the buyer (initials in public)",
       " -> ".join(f"{l['owner']} (block {l['block']})" for l in chain["links"]))
    rkey = call("GET", "/api/public/records-key")
    pub = call("GET", f"/api/public/records/{app_id}", raw=True).text
    v, text = verify_cleartext(rkey["publicKey"], pub)
    if not v.valid or seller.name in pub or "38,50,000" in pub or not rkey["onLedger"]:
        fail("public record", f"{v.reason}; names hidden: {seller.name not in pub}")
    ok("public record: OpenPGP-signed, Records Office key attested on the ledger, no names or amounts",
       f"{len(pub.splitlines())} lines, key {v.fingerprint[:16]}...")
    mine = call("GET", f"/api/citizen/records/{app_id}", seller.token, raw=True).text
    theirs = call("GET", f"/api/citizen/records/{app_id}", buyer.token, raw=True).text
    vs, _ = verify_cleartext(rkey["publicKey"], mine)
    if not vs.valid or seller.name not in mine or "Rs. 38,50,000" not in mine or "(seller)" not in mine \
            or "(buyer)" not in theirs:
        fail("party records", vs.reason)
    call("GET", f"/api/citizen/records/{app_id}", stranger.token, expect=403)
    ok("party copies: seller's and buyer's own, signed, with names and amount; a stranger is refused")
    res = call("POST", "/api/public/records/verify", json={"record": mine})
    bad = call("POST", "/api/public/records/verify", json={"record": mine.replace("38,50,000", "3,85,000")})
    if not res["valid"] or not res["keyAttestedOnLedger"] or bad["valid"]:
        fail("record verification", json.dumps(res)[:200])
    ok("the record verifier accepts the record and rejects an altered amount", bad["reason"][:60])

    # -- search, ledger, sketch, events, agent ------------------------------------------------
    hits = call("GET", f"/api/applications?q={tag}", tokens["collector1"])["applications"]
    none = call("GET", "/api/applications?q=zz-no-such-file-zz", tokens["collector1"])["applications"]
    if not hits or none:
        fail("search", f"hits={len(hits)} none={len(none)}")
    ok("search finds the file; nonsense query returns an empty list")
    blocks = call("GET", "/api/ledger/blocks?limit=3", tokens["collector1"])
    blist = blocks.get("blocks", blocks if isinstance(blocks, list) else [])
    if not blist:
        fail("ledger", json.dumps(blocks)[:200])
    ok("ledger explorer", f"tip block {blist[0].get('number')}")
    svg = call("GET", f"/api/public/sketch?surveyNo={survey_no}")
    if not svg.text.startswith("<svg"):
        fail("sketch", svg.text[:80])
    call("GET", "/api/public/sketch?surveyNo=SY-NOPE", expect=404)
    ok("parcel sketch SVG (and 404 for unknown parcels)")
    with http.stream("GET", "/api/events", headers={"Authorization": f"Bearer {tokens['clerk1']}"}, timeout=10) as resp:
        for line in resp.iter_lines():
            if line.startswith("data:"):
                ev = json.loads(line[5:])
                ok("live block feed (SSE)", f"block {ev.get('number')}")
                break
    answer, kinds = "", set()
    with http.stream("POST", "/api/agent/ask", headers={"Authorization": f"Bearer {tokens['collector1']}"},
                     json={"question": "Why is this file clear?", "runId": rid}, timeout=60) as resp:
        for line in resp.iter_lines():
            if line.startswith("data:"):
                ev = json.loads(line[5:])
                kinds.add(ev.get("type"))
                if ev.get("type") == "token":
                    answer += ev.get("text", "")
                if ev.get("type") in ("done", "error"):
                    break
    if "token" not in kinds and "done" not in kinds:
        fail("agent ask", f"events {kinds}")
    ok("agent Q&A streamed", f"{len(answer)} chars, events {sorted(k for k in kinds if k)}")
    print(f"\n{G}All {passed} checks passed.{X}  File {app_id}")


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print(f"{R}The gateway is not reachable at {GW}. Start it: ./startup.sh{X}")
        sys.exit(2)
