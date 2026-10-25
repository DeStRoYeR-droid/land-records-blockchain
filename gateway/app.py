"""
The gateway: the REST API behind the three dashboards.

It is a CLIENT of the blockchain, exactly like Fabric's Gateway service: it
holds each officer's identity (their CA-issued key), and when clerk1 clicks
"Open transfer file", it signs that proposal with clerk1's key, collects
endorsements from all three departments' peers, submits it to the orderers
and waits for the commit. The chaincode -- not the gateway -- has the last
word on who may do what; the gateway's own role checks only give friendlier
errors earlier.

Groups of endpoints
  /api/public/*      landing page, verification (no sign-in)
  /api/auth/*        officer sign-in;  /api/citizen/{challenge,register,login}
  /api/officer/*, /api/applications*, /api/parcels*, /api/ledger/*   officials
  /api/citizen/*     a citizen's land, documents, signed transfer intents, reports
  /api/agent/*       the validator's status, runs, live stream, Q&A (proxied)
  /api/events        live block feed (Server-Sent Events)
  /api/public/geo/*  the geospatial database: map window, near a point, at a point, neighbours, export
  /api/public/ownership, /api/public/records/*, /api/citizen/records/*
                     chain of ownership; PGP-signed transfer records (public copy; party copies)
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from typing import Any, AsyncIterator

import httpx
from fastapi import Body, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

from chaincode.landrec import didkey
from chaincode.landrec.landrec import canonical
from nodes.common import config, crypto, log
from nodes.common.msp import SigningIdentity
from nodes.common.web import read_sse, sse_response
from nodes.sdk import ChaincodeError, CommitError, EndorsementMismatch, Gateway, NetworkUnavailable

from chaincode.landrec import geometry
from geo.store import bbox_param

from . import auth, records, settings
from .geo_index import GeoIndex
from .sketch import parcel_svg

logger = log.get("gateway")


# ---------------------------------------------------------------------------
# Ledger access with each officer's own identity
# ---------------------------------------------------------------------------

def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ChaincodeError):
        msg = exc.message
        if msg.startswith("ACCESS DENIED"):
            return HTTPException(403, msg)
        return HTTPException(404 if msg.endswith("does not exist") else 409, msg)
    if isinstance(exc, CommitError):
        return HTTPException(409, f"The network ordered the transaction but marked it {exc.code}: {exc.detail}")
    if isinstance(exc, (NetworkUnavailable, EndorsementMismatch)):
        return HTTPException(503, f"The network is unavailable: {exc}")
    return HTTPException(500, str(exc))


class Ledger:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.gws: dict[str, Gateway] = {}
        self.msp_of = {u: m for m, org in config.topology()["organizations"].items() for u in (org.get("users") or {})}

    def gw(self, user: str) -> Gateway:
        if user not in self.gws:
            msp = self.msp_of[user]
            ident = SigningIdentity.from_msp_dir(config.user_msp_dir(msp, user), msp)
            self.gws[user] = Gateway.for_identity(ident, http=self.http)
        return self.gws[user]

    async def q(self, user: str, fn: str, *args: str) -> Any:
        try:
            return await self.gw(user).evaluate_json("landrec", fn, *args)
        except Exception as exc:
            raise _http_error(exc) from exc

    async def tx(self, user: str, fn: str, *args: str) -> dict[str, Any]:
        try:
            res = await self.gw(user).submit("landrec", fn, *args)
        except Exception as exc:
            raise _http_error(exc) from exc
        return {"result": res.json(), "txId": res.tx_id, "block": res.block, "endorsers": res.endorsers}


# ---------------------------------------------------------------------------
# DigiLocker
# ---------------------------------------------------------------------------

class DigiLocker:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self.url = settings.DIGILOCKER_URL

    async def _call(self, method: str, path: str, **kw) -> Any:
        try:
            resp = await self.http.request(method, f"{self.url}{path}", timeout=10, **kw)
        except httpx.HTTPError as exc:
            raise HTTPException(503, f"DigiLocker is unreachable ({type(exc).__name__})") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = resp.text[:200]
            raise HTTPException(resp.status_code if resp.status_code < 500 else 502, f"DigiLocker: {detail}")
        return resp.json()

    async def signup(self, name: str, mobile: str, last4: str) -> dict[str, Any]:
        return await self._call("POST", "/users/signup", json={"name": name, "mobile": mobile, "aadhaar_last4": last4})

    async def user(self, dl_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/users/{dl_id}")

    async def verify_otp(self, dl_id: str, otp: str) -> dict[str, Any]:
        return await self._call("POST", f"/users/{dl_id}/otp/verify", json={"otp": otp})

    async def documents(self, dl_id: str) -> list[dict[str, Any]]:
        return await self._call("GET", f"/users/{dl_id}/documents")

    async def access_log(self, dl_id: str) -> list[dict[str, Any]]:
        return await self._call("GET", f"/users/{dl_id}/access-log")

    async def issue(self, dl_id: str, doc_type: str, org: str, description: str, content: str) -> dict[str, Any]:
        return await self._call("POST", "/issuer/documents", json={"digilocker_id": dl_id, "doc_type": doc_type,
                                                                    "org_name": org, "description": description,
                                                                    "content": content})

    async def holder(self, owner_ref: str, purpose: str) -> dict[str, Any]:
        return await self._call("GET", f"/department/holders/{owner_ref}", params={"purpose": purpose},
                                headers={"X-Department-Key": settings.DIGILOCKER_REVENUE_KEY})

    async def consent_fetch(self, requester: str, dl_id: str, otp: str, doc_type: str, org: str, doc_id: str) -> dict[str, Any]:
        """DigiLocker consent flow: request -> citizen's OTP -> fetch exactly this document."""
        req = await self._call("POST", "/consent/request", json={"requester_name": requester,
                                                                  "redirect_url": "https://landchain.example/callback"})
        await self._call("POST", f"/consent/{req['id']}/verify-otp", json={"digilocker_id": dl_id, "otp": otp})
        return await self._call("POST", f"/consent/{req['id']}/document",
                                json={"doc_type": doc_type, "org_name": org, "consent": "Y", "doc_id": doc_id})


def owner_ref(dl_id: str) -> str:
    return hashlib.sha256(f"landchain-owner:{dl_id}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Off-chain inbox: transfer intents waiting for signatures
# ---------------------------------------------------------------------------

class IntentStore:
    def __init__(self):
        self.db = sqlite3.connect(config.data_dir("gateway") / "gateway.db", check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, status TEXT, seller_ref TEXT,"
                        " buyer_ref TEXT, created REAL, data TEXT)")
        self.db.commit()

    def save(self, rec: dict[str, Any]) -> None:
        self.db.execute("INSERT INTO intents (id, status, seller_ref, buyer_ref, created, data) VALUES (?,?,?,?,?,?)"
                        " ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data",
                        (rec["id"], rec["status"], rec["intent"]["sellerRef"], rec["intent"]["buyerRef"],
                         rec.get("createdTs", time.time()), json.dumps(rec)))
        self.db.commit()

    def get(self, intent_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT data FROM intents WHERE id=?", (intent_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def for_ref(self, ref: str) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT data FROM intents WHERE seller_ref=? OR buyer_ref=? ORDER BY created DESC",
                               (ref, ref)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def by_status(self, *statuses: str) -> list[dict[str, Any]]:
        rows = self.db.execute(f"SELECT data FROM intents WHERE status IN ({','.join('?' * len(statuses))})"
                               " ORDER BY created DESC", statuses).fetchall()
        return [json.loads(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# Document templates the Sub-Registrar issues into DigiLocker
# ---------------------------------------------------------------------------

def deed_text(f: dict[str, Any]) -> str:
    amount = int(f["considerationINR"])
    s = f"{amount:,}"
    if amount >= 100000:  # Indian grouping: 45,00,000
        head, tail = str(amount)[:-3], str(amount)[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        s = ",".join([head] + groups) + "," + tail if head else ",".join(groups) + "," + tail
    return (f"SALE DEED\nSurvey No: {f['surveyNo']}, {f['village']}, {f['district']}, Telangana\n"
            f"Extent: {f['extentSqm']} sq.m\nVendor: {f['vendor']}\nPurchaser: {f['purchaser']}\n"
            f"Consideration: Rs. {s}\nRegistered at {settings.SRO}")


def ec_text(f: dict[str, Any]) -> str:
    return (f"ENCUMBRANCE CERTIFICATE\nSurvey No: {f['surveyNo']}\nPeriod searched: {f.get('periodFrom', 2011)}-"
            f"{f.get('periodTo', dt.date.today().year)}\nResult: {f.get('result') or 'NIL encumbrance'}")


QUEUES = {
    "registrar": [("SUBMITTED", "Forward to Revenue", lambda a: len(a["documents"]) > 0)],
    "clerk": [("SUBMITTED", "Anchor documents", lambda a: True)],
    "verifier": [("WITH_REVENUE", "Verify documents", lambda a: True),
                 ("SURVEYED", "Forward to the Collectorate", lambda a: True)],
    "surveyor": [("DOCS_VERIFIED", "Record the field survey", lambda a: True)],
    "collector": [("WITH_COLLECTORATE", "Decide", lambda a: True), ("FLAGGED", "Review flagged file", lambda a: True)],
}


def _matches(q: str, *fields: Any) -> bool:
    q = q.strip().lower()
    return not q or any(q in str(f or "").lower() for f in fields)


def _initials(name: str) -> str:
    """The public register shows an owner as initials only ("S. R.")."""
    return " ".join(f"{p[0].upper()}." for p in str(name or "").split() if p[:1].isalpha())


def _public_parcel(x: dict[str, Any]) -> dict[str, Any]:
    return {"surveyNo": x["surveyNo"], "village": x["village"], "district": x["district"], "areaSqm": x["areaSqm"],
            "status": x["status"], "ownerInitials": _initials(x["ownerName"]), "registeredAt": x.get("registeredAt"),
            "updatedAt": x.get("updatedAt"), "lastTransferAt": (x.get("lastTransfer") or {}).get("at"),
            "boundary": x.get("boundary"), "boundaryAreaSqm": x.get("boundaryAreaSqm"), "centroid": x.get("centroid"),
            "perimeterM": x.get("perimeterM")}


def build_app():
    http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0))
    ledger = Ledger(http)
    dl = DigiLocker(http)
    intents = IntentStore()
    challenges = auth.Challenges()
    officers = auth.officers()
    agent_url = config.url("agent")
    home_peer = config.url(config.org_peer("Org1MSP"))
    geo = GeoIndex(ledger, http)
    records_key = records.load_or_create_key()
    tx_blocks: dict[str, int | None] = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        geo.start()
        yield
        await geo.stop()
        await http.aclose()

    from nodes.common.web import create_app
    app = create_app("LandChain gateway", "REST API for the officials', citizens' and agent dashboards.", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_methods=["*"],
                       allow_headers=["*"], expose_headers=["Content-Disposition"])

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception):
        logger.exception("unhandled error")
        return JSONResponse(status_code=500, content={"error": f"Internal error: {type(exc).__name__}: {exc}"})

    @app.exception_handler(HTTPException)
    async def _http_exc(_req: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    # =====================================================================
    # Health, network, public
    # =====================================================================

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "gateway"}

    async def _get(url: str, timeout: float = 2.0) -> Any:
        try:
            resp = await http.get(url, timeout=timeout)
            return resp.json() if resp.status_code == 200 else None
        except Exception:
            return None

    @app.get("/api/network")
    async def network():
        names = config.nodes_of_kind("orderer") + config.nodes_of_kind("peer") + config.nodes_of_kind("ca")
        urls = [f"{config.url(n)}/status" if config.node(n)["kind"] != "ca" else f"{config.url(n)}/health" for n in names]
        extra = {"digilocker": f"{config.url('digilocker')}/health", "agent": f"{agent_url}/health"}
        results = await asyncio.gather(*(_get(u) for u in urls + list(extra.values())))
        nodes = []
        for name, res in zip(names + list(extra), results):
            spec = config.node(name)
            entry: dict[str, Any] = {"name": name, "kind": spec["kind"], "org": spec.get("org"), "port": spec["port"],
                                     "up": res is not None}
            if res and spec["kind"] == "orderer":
                r = res.get("raft") or {}
                entry.update(height=res.get("height"), state=r.get("state"), term=r.get("term"), leader=r.get("leader"),
                             commitIndex=r.get("commitIndex"), lastLogIndex=r.get("lastLogIndex"),
                             msSinceLeaderContact=r.get("msSinceLeaderContact"), events=r.get("events", [])[-6:],
                             pendingTx=res.get("pendingTx"), lastBlockHash=res.get("lastBlockHash"))
            elif res and spec["kind"] == "peer":
                cc = (res.get("chaincodes") or {}).get("landrec", {})
                entry.update(height=res.get("height"), lastBlockHash=res.get("lastBlockHash"),
                             deliverSource=res.get("deliverSource"), worldStateKeys=res.get("worldStateKeys"),
                             chaincode={k: cc.get(k) for k in ("version", "sequence", "matches", "policy")},
                             txCodes=res.get("txCodes"), stats=res.get("stats"),
                             recentInvalid=(res.get("recentInvalid") or [])[-5:], lastCommitAgoS=res.get("lastCommitAgoS"))
            nodes.append(entry)
        leaders = [n["leader"] for n in nodes if n["kind"] == "orderer" and n.get("leader")]
        leader = max(set(leaders), key=leaders.count) if leaders else None
        heights = [n["height"] for n in nodes if n["kind"] == "peer" and n.get("height") is not None]
        return {"channel": config.channel_name(), "leader": leader, "height": max(heights) if heights else None,
                "nodes": nodes, "checkedAt": crypto.iso(crypto.utcnow())}

    @app.get("/api/public/summary")
    async def public_summary():
        apps, parcels = await asyncio.gather(ledger.q("portal1", "ListApplications", "ALL"),
                                             ledger.q("portal1", "ListParcels"))
        by_status: dict[str, int] = {}
        for a in apps:
            by_status[a["status"]] = by_status.get(a["status"], 0) + 1
        st = await _get(f"{home_peer}/status")
        agent = await _get(f"{agent_url}/status", timeout=4)
        villages = {p["village"] for p in parcels}
        verdicts: dict[str, int] = {}
        for a in apps:
            for v in a.get("validations", []):
                verdicts[v["verdict"]] = verdicts.get(v["verdict"], 0) + 1
        decided = sorted((a for a in apps if a.get("decision")), key=lambda a: a["decision"]["at"], reverse=True)[:6]
        return {"parcels": len(parcels), "villages": len(villages), "applications": len(apps), "byStatus": by_status,
                "height": st and st.get("height"), "reports": sum(verdicts.values()), "verdicts": verdicts,
                "recentDecisions": [{"surveyNo": a["surveyNo"], "outcome": a["decision"]["outcome"],
                                     "at": a["decision"]["at"]} for a in decided],
                "agent": _agent_public(agent)}

    def _agent_public(st: dict[str, Any] | None) -> dict[str, Any]:
        if not st:
            return {"online": False}
        return {"online": True, "name": st["name"], "explainer": {k: st["explainer"].get(k) for k in ("engine", "model")},
                "pgpFingerprint": st["identity"]["pgpFingerprint"],
                "pgpFingerprintFormatted": st["identity"]["pgpFingerprintFormatted"],
                "attested": (st.get("onChainKey") or {}).get("status") == "ACTIVE",
                "counters": st["counters"], "listening": st["listener"]["connected"],
                "framework": st.get("framework")}

    @app.get("/api/public/parcels")
    async def public_parcels(q: str = ""):
        """The public land register: no names (initials only), no ownerRefs."""
        parcels = await ledger.q("portal1", "ListParcels")
        out = [_public_parcel(x) for x in parcels if _matches(q, x["surveyNo"], x["village"], x["district"])]
        out.sort(key=lambda x: x["surveyNo"])
        return {"parcels": out, "query": q, "total": len(parcels)}

    @app.get("/api/public/parcel")
    async def public_parcel(surveyNo: str):
        parcel, history = await asyncio.gather(ledger.q("portal1", "ReadParcel", surveyNo),
                                               ledger.q("portal1", "ParcelHistory", surveyNo))
        events = []
        for h in history:
            v = h.get("value") or {}
            t = v.get("lastTransfer")
            if not events:
                events.append({"kind": "registered", "at": v.get("registeredAt") or h["timestamp"], "txId": h["txId"],
                               "ownerInitials": _initials(v.get("ownerName", ""))})
            elif v.get("status") == "TRANSFER_PENDING" and events[-1]["kind"] != "pending":
                events.append({"kind": "pending", "at": h["timestamp"], "txId": h["txId"],
                               "appId": v.get("pendingApplication")})
            elif t and t.get("txId") == h["txId"]:
                events.append({"kind": "transferred", "at": t["at"], "txId": h["txId"], "appId": t.get("appId"),
                               "ownerInitials": _initials(t.get("toName", ""))})
            elif v.get("status") == "ACTIVE" and events[-1]["kind"] == "pending":
                events.append({"kind": "released", "at": h["timestamp"], "txId": h["txId"]})
        try:
            ownership = await _ownership(surveyNo)
        except HTTPException:
            ownership = None
        return {"parcel": _public_parcel(parcel), "events": events, "ownership": ownership}

    @app.get("/api/public/agent")
    async def public_agent():
        return _agent_public(await _get(f"{agent_url}/status", timeout=4))

    @app.get("/api/public/agent-key")
    async def agent_key():
        try:
            pub = (await http.get(f"{agent_url}/public-key.asc", timeout=4)).text
        except httpx.HTTPError:
            pub = None
        try:
            onchain = await ledger.q("portal1", "ReadAgentKey", settings.AGENT_NAME)
        except HTTPException:
            onchain = None
        return {"agent": settings.AGENT_NAME, "publicKey": pub or (onchain or {}).get("publicKey"),
                "publicKeySource": "agent" if pub else "ledger", "onChain": onchain}

    @app.get("/api/public/verify/report/{sha}")
    async def verify_report(sha: str):
        return await ledger.q("portal1", "VerifyValidationReport", sha.strip().lower())

    @app.get("/api/public/verify/document/{sha}")
    async def verify_document(sha: str):
        return await ledger.q("portal1", "VerifyDocument", sha.strip().lower())

    @app.get("/api/public/sketch")
    async def sketch(surveyNo: str):
        try:
            parcel = await ledger.q("portal1", "ReadParcel", surveyNo)
        except HTTPException:
            return Response(status_code=404, content="no such parcel")
        boundaries = None
        apps = [a for a in await ledger.q("portal1", "ListApplications", "ALL") if a["surveyNo"] == surveyNo and a.get("survey")]
        if apps:
            boundaries = apps[-1]["survey"]["boundaries"]
        svg = parcel_svg(parcel["surveyNo"], parcel["areaSqm"], parcel["village"], parcel["district"], boundaries,
                         parcel["status"])
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=60"})

    # =====================================================================
    # Geospatial database (public): search the land register on a map
    # =====================================================================

    def _bbox(text: str | None):
        try:
            return bbox_param(text)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.get("/api/public/geo/parcels")
    async def geo_parcels(bbox: str | None = None, q: str = "", status: str = "", village: str = "",
                          minArea: float | None = None, maxArea: float | None = None, limit: int = 500):
        """Parcels as GeoJSON: inside the map window (`bbox`=minLng,minLat,maxLng,maxLat), filtered by text, status, area."""
        feats = geo.store.search(q=q, bbox=_bbox(bbox), status=status, min_area=minArea, max_area=maxArea,
                                 village=village, limit=max(1, min(limit, 2000)))
        return geo.store.collection(feats, query={"bbox": bbox, "q": q, "status": status, "village": village,
                                                  "minArea": minArea, "maxArea": maxArea}, count=len(feats))

    @app.get("/api/public/geo/near")
    async def geo_near(lng: float, lat: float, radius: float = 500, limit: int = 20):
        feats = geo.store.near(lng, lat, max(1.0, min(radius, 20000.0)), max(1, min(limit, 200)))
        return geo.store.collection(feats, centre=[lng, lat], radiusM=radius, count=len(feats))

    @app.get("/api/public/geo/at")
    async def geo_at(lng: float, lat: float):
        return {"point": [lng, lat], "parcel": geo.store.at(lng, lat)}

    @app.get("/api/public/geo/parcel")
    async def geo_parcel(surveyNo: str, within: float = 1.0):
        row = geo.store.get(surveyNo)
        if not row:
            raise HTTPException(404, f"Parcel {surveyNo} has no boundary in the geospatial database")
        nb = geo.store.neighbours(surveyNo, max(0.0, min(within, 2000.0)))
        return {"parcel": geo.store.feature(row), "neighbours": geo.store.collection(nb, withinM=within, count=len(nb))}

    @app.get("/api/public/geo/overlaps")
    async def geo_overlaps(surveyNo: str):
        """Registered parcels whose boundaries overlap this parcel's (sharing an edge is not an overlap)."""
        row = geo.store.get(surveyNo)
        if not row:
            raise HTTPException(404, f"Parcel {surveyNo} has no boundary in the geospatial database")
        feats = geo.store.overlaps(geo.store.ring(row), exclude=surveyNo)
        return geo.store.collection(feats, surveyNo=surveyNo, count=len(feats))

    @app.get("/api/public/geo/stats")
    async def geo_stats():
        return geo.status()

    @app.get("/api/public/geo/export.geojson")
    async def geo_export_geojson():
        body = json.dumps(geo.store.collection(geo.store.all_features(), name="LandChain parcels"), indent=1)
        return Response(body, media_type="application/geo+json",
                        headers={"Content-Disposition": 'attachment; filename="landchain-parcels.geojson"'})

    @app.get("/api/public/geo/export.gpkg")
    async def geo_export_gpkg():
        import sqlite3 as _sq
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:                 # a consistent copy, via SQLite's backup API
            path = f"{tmp}/landchain-parcels.gpkg"
            dst = _sq.connect(path)
            with geo.store.lock:
                geo.store.db.backup(dst)
            dst.close()
            data = open(path, "rb").read()
        return Response(data, media_type="application/geopackage+sqlite3",
                        headers={"Content-Disposition": 'attachment; filename="landchain-parcels.gpkg"'})

    # =====================================================================
    # Chain of ownership and signed transfer records
    # =====================================================================

    async def _blocks_of(tx_ids: list[str]) -> dict[str, int | None]:
        want = [t for t in dict.fromkeys(tx_ids) if t and t not in tx_blocks]

        async def one(t: str) -> None:
            res = await _get(f"{home_peer}/txs/{t}")
            if res:
                tx_blocks[t] = res.get("block")
        await asyncio.gather(*(one(t) for t in want))
        return {t: tx_blocks.get(t) for t in tx_ids if t}

    async def _records_key_on_ledger() -> dict[str, Any] | None:
        try:
            rec = await ledger.q("portal1", "ReadRecordsKey")
        except HTTPException:
            return None
        return {k: rec.get(k) for k in ("pgpFingerprint", "status", "attestedBy", "attestedAt")} \
            if rec.get("pgpFingerprint") == records_key.fingerprint else None

    async def _ownership(survey_no: str, public: bool = True) -> dict[str, Any]:
        chain, apps = await asyncio.gather(ledger.q("portal1", "OwnershipChain", survey_no),
                                           ledger.q("portal1", "ListApplications", "ALL"))
        mine = sorted((a for a in apps if a["surveyNo"] == survey_no), key=lambda a: a["trail"][0]["at"] if a["trail"] else "")
        blocks = await _blocks_of([l["sinceTxId"] for l in chain["links"]])
        links = [{"seq": l["seq"], "owner": _initials(l["ownerName"]) if public else l["ownerName"],
                  "since": l["since"], "until": l["until"], "acquired": l["acquired"], "appId": l.get("appId"),
                  "txId": l["sinceTxId"], "block": blocks.get(l["sinceTxId"]),
                  "decidedBy": l.get("decidedBy") if not public else None} for l in chain["links"]]
        transfers = [{"appId": a["appId"], "status": a["status"], "statusText": records.STATUS_TEXT.get(a["status"], a["status"]),
                      "openedAt": a["trail"][0]["at"] if a["trail"] else None, "updatedAt": a["updatedAt"],
                      "seller": _initials(a["sellerName"]), "buyer": _initials(a["buyerName"]),
                      "decision": (a.get("decision") or {}).get("outcome"), "decidedAt": (a.get("decision") or {}).get("at"),
                      "verdicts": [v["verdict"] for v in a.get("validations", [])],
                      "publicRecord": f"/api/public/records/{a['appId']}"} for a in mine]
        return {"surveyNo": survey_no, "owners": chain["owners"], "links": links, "transfers": transfers}

    @app.get("/api/public/ownership")
    async def public_ownership(surveyNo: str):
        """The chain of ownership of a parcel (owners as initials) and every transfer file ever opened on it."""
        return await _ownership(surveyNo)

    async def _record_ctx(app_id: str) -> dict[str, Any]:
        a = await ledger.q("portal1", "ReadApplication", app_id)
        parcel, chain, st, key_rec = await asyncio.gather(
            ledger.q("portal1", "ReadParcel", a["surveyNo"]), ledger.q("portal1", "OwnershipChain", a["surveyNo"]),
            _get(f"{home_peer}/status"), _records_key_on_ledger())
        txs = [t["txId"] for t in a["trail"]] + [v.get("txId") for v in a.get("validations", [])] + \
              [l["sinceTxId"] for l in chain["links"]]
        return {"application": a, "parcel": parcel, "chain": chain, "height": (st or {}).get("height"),
                "blocks": await _blocks_of([t for t in txs if t]), "keyOnLedger": key_rec}

    def _download(rec: dict[str, Any]) -> Response:
        return Response(rec["text"], media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{rec["filename"]}"',
                                 "X-Record-Id": rec["recordId"], "Cache-Control": "no-store"})

    @app.get("/api/public/records/{app_id}")
    async def public_record(app_id: str, format: str = "asc"):
        """The public copy of a transfer record, OpenPGP cleartext-signed by the Records Office."""
        rec = records.build("public", await _record_ctx(app_id), records_key)
        return rec["data"] if format == "json" else _download(rec)

    @app.get("/api/citizen/records/{app_id}")
    async def party_record(app_id: str, format: str = "asc", p: auth.Principal = Depends(auth.citizen)):
        """A party's own copy: only the seller or the buyer of this transfer can download it."""
        ctx = await _record_ctx(app_id)
        a, ref = ctx["application"], p.claims["ownerRef"]
        if ref not in (a["sellerRef"], a["buyerRef"]):
            raise HTTPException(403, "Only the seller and the buyer of this transfer can download a party copy")
        role = "seller" if ref == a["sellerRef"] else "buyer"
        intent = next((r for r in intents.for_ref(ref) if r.get("appId") == app_id), None)
        runs = [r for r in await _agent_runs(app_id) if r.get("status") == "anchored"]
        explanation = None
        if runs:
            full = await _get(f"{agent_url}/runs/{runs[0]['runId']}", timeout=5) or {}
            explanation = ((full.get("explanation") or {}).get("citizen") or {}).get("text")
        try:
            log_entries = [e for e in await dl.access_log(p.claims["digilockerId"]) if app_id in (e.get("purpose") or "")]
        except HTTPException:
            log_entries = []
        ctx.update(intent=intent and {k: intent.get(k) for k in ("intent", "sellerSignature", "buyerSignature")},
                   citizenExplanation=explanation, accessLog=log_entries)
        rec = records.build("party", ctx, records_key, recipient={**p.claims, "role": role})
        return rec["data"] if format == "json" else _download(rec)

    @app.get("/api/public/records-key")
    async def records_key_info():
        return {"publicKey": records_key.public_armored, "pgpFingerprint": records_key.fingerprint,
                "userId": records_key.pgp.user_id, "onLedger": await _records_key_on_ledger()}

    @app.post("/api/public/records/verify")
    async def verify_record(body: dict = Body(...)):
        """Check a downloaded record: its signature, and that the signing key is the one the ledger attests."""
        message = str(body.get("record", ""))
        if "BEGIN PGP SIGNED MESSAGE" not in message:
            raise HTTPException(400, "Paste or upload a LandChain transfer record (.asc)")
        res = records.verify(message, records_key.public_armored)
        on_ledger = await _records_key_on_ledger()
        res["keyAttestedOnLedger"] = bool(on_ledger and res.get("fingerprint") == on_ledger["pgpFingerprint"])
        res["attestation"] = on_ledger
        if res.get("applicationId"):
            try:
                a = await ledger.q("portal1", "ReadApplication", res["applicationId"])
                res["currentStatus"] = a["status"]
            except HTTPException:
                res["currentStatus"] = None
        return res

    # =====================================================================
    # Sign-in
    # =====================================================================

    @app.post("/api/auth/officer")
    async def officer_login(body: dict = Body(...)):
        user = str(body.get("username", "")).strip()
        if user not in officers or not auth.password_ok(user, str(body.get("password", ""))):
            raise HTTPException(401, "Wrong username or password")
        who = await ledger.q(user, "WhoAmI")  # what the chaincode reads from this officer's certificate
        profile = {**officers[user], "certificate": who}
        return {"token": auth.issue_token({"kind": "officer", **{k: v for k, v in officers[user].items()}}),
                "user": profile}

    @app.get("/api/auth/officers")
    async def officer_list():
        return [{k: o[k] for k in ("username", "org", "title", "duty", "role", "mspId")} for o in officers.values()]

    @app.get("/api/me")
    async def me(p: auth.Principal = Depends(auth.any_principal)):
        return {"kind": p.kind, **p.claims}

    @app.post("/api/citizen/challenge")
    async def citizen_challenge(body: dict = Body(...)):
        purpose = body.get("purpose", "login")
        subject = str(body.get("digilockerId") or body.get("did") or "")
        if purpose not in ("login", "register") or not subject:
            raise HTTPException(400, "purpose (login|register) and digilockerId or did are required")
        return {"challenge": challenges.issue(purpose, subject), "expiresIn": 300}

    @app.post("/api/citizen/register")
    async def citizen_register(body: dict = Body(...)):
        name, mobile, last4 = (str(body.get(k, "")).strip() for k in ("name", "mobile", "aadhaarLast4"))
        did, challenge, signature = body.get("did", ""), body.get("challenge", ""), body.get("signature", "")
        if len(name) < 3 or not mobile.isdigit() or len(mobile) != 10 or not last4.isdigit() or len(last4) != 4:
            raise HTTPException(400, "Name, a 10-digit mobile number and the last 4 digits of Aadhaar are required")
        if not challenges.take(challenge, "register", did):
            raise HTTPException(400, "The registration challenge expired; try again")
        message = canonical({"type": "landchain.register/v1", "did": did, "challenge": challenge})
        if not didkey.verify(did, message, _b64d(signature)):
            raise HTTPException(400, "The key proof does not verify: the browser must sign with the key behind this DID")
        account = await dl.signup(name, mobile, last4)
        ref = owner_ref(account["digilockerId"])
        await ledger.tx("portal1", "RegisterCitizen", ref, did)
        claims = {"kind": "citizen", "digilockerId": account["digilockerId"], "ownerRef": ref, "did": did, "name": name}
        return {"token": auth.issue_token(claims), "citizen": claims}

    @app.post("/api/citizen/login")
    async def citizen_login(body: dict = Body(...)):
        dl_id = str(body.get("digilockerId", "")).strip()
        challenge, signature = body.get("challenge", ""), body.get("signature", "")
        await dl.verify_otp(dl_id, str(body.get("otp", "")))
        if not challenges.take(challenge, "login", dl_id):
            raise HTTPException(400, "The sign-in challenge expired; try again")
        ref = owner_ref(dl_id)
        try:
            record = await ledger.q("portal1", "ReadCitizen", ref)
        except HTTPException:
            raise HTTPException(404, "This DigiLocker account has no LandChain identity yet; register first")
        message = canonical({"type": "landchain.login/v1", "digilockerId": dl_id, "challenge": challenge})
        if not didkey.verify(record["did"], message, _b64d(signature)):
            raise HTTPException(401, "Your key does not match the DID registered on the ledger for this account")
        user = await dl.user(dl_id)
        claims = {"kind": "citizen", "digilockerId": dl_id, "ownerRef": ref, "did": record["did"], "name": user["name"]}
        return {"token": auth.issue_token(claims), "citizen": claims}

    # =====================================================================
    # Officials
    # =====================================================================

    async def _agent_runs(app_id: str) -> list[dict[str, Any]]:
        data = await _get(f"{agent_url}/runs?appId={app_id}&limit=10", timeout=4)
        return (data or {}).get("runs", [])

    @app.get("/api/officer/summary")
    async def officer_summary(p: auth.Principal = Depends(auth.officer)):
        apps = await ledger.q(p.claims["username"], "ListApplications", "ALL")
        by_status: dict[str, int] = {}
        for a in apps:
            by_status[a["status"]] = by_status.get(a["status"], 0) + 1
        queue = []
        for status, action, ok in QUEUES.get(p.role or "", []):
            for a in apps:
                if a["status"] == status and ok(a):
                    queue.append({"appId": a["appId"], "surveyNo": a["surveyNo"], "status": a["status"],
                                  "action": action, "seller": a["sellerName"], "buyer": a["buyerName"],
                                  "updatedAt": a["updatedAt"],
                                  "validation": (a.get("validations") or [{}])[-1].get("verdict")})
        ready = intents.by_status("ready") if p.role == "clerk" else []
        recent = sorted(apps, key=lambda a: a["updatedAt"], reverse=True)[:8]
        return {"byStatus": by_status, "total": len(apps), "queue": sorted(queue, key=lambda x: x["updatedAt"]),
                "readyIntents": len(ready),
                "recent": [{"appId": a["appId"], "status": a["status"], "surveyNo": a["surveyNo"],
                            "updatedAt": a["updatedAt"], "last": a["trail"][-1] if a["trail"] else None} for a in recent]}

    @app.get("/api/applications")
    async def list_applications(q: str = "", status: str = "ALL", p: auth.Principal = Depends(auth.officer)):
        apps = await ledger.q(p.claims["username"], "ListApplications", status or "ALL")
        out = [a for a in apps if _matches(q, a["appId"], a["surveyNo"], a["sellerName"], a["buyerName"], a["status"])]
        out.sort(key=lambda a: a["updatedAt"], reverse=True)
        return {"applications": out, "query": q, "total": len(apps)}

    @app.get("/api/applications/{app_id}")
    async def get_application(app_id: str, p: auth.Principal = Depends(auth.officer)):
        user = p.claims["username"]
        a = await ledger.q(user, "ReadApplication", app_id)
        parcel, history, runs = await asyncio.gather(ledger.q(user, "ReadParcel", a["surveyNo"]),
                                                     ledger.q(user, "ApplicationHistory", app_id), _agent_runs(app_id))
        return {"application": a, "parcel": parcel, "versions": len(history), "agentRuns": runs}

    @app.get("/api/parcels")
    async def list_parcels(q: str = "", p: auth.Principal = Depends(auth.officer)):
        parcels = await ledger.q(p.claims["username"], "ListParcels")
        out = [x for x in parcels if _matches(q, x["surveyNo"], x["village"], x["district"], x["ownerName"], x["status"])]
        out.sort(key=lambda x: x["updatedAt"], reverse=True)
        return {"parcels": out, "query": q, "total": len(parcels)}

    @app.get("/api/parcel")
    async def get_parcel(surveyNo: str, p: auth.Principal = Depends(auth.officer)):
        user = p.claims["username"]
        parcel, history, apps = await asyncio.gather(ledger.q(user, "ReadParcel", surveyNo),
                                                     ledger.q(user, "ParcelHistory", surveyNo),
                                                     ledger.q(user, "ListApplications", "ALL"))
        return {"parcel": parcel, "history": history, "ownership": await _ownership(surveyNo, public=False),
                "applications": [a for a in apps if a["surveyNo"] == surveyNo]}

    @app.get("/api/citizens/lookup")
    async def citizen_lookup(digilockerId: str, p: auth.Principal = Depends(auth.officer)):
        user = await dl.user(digilockerId.strip())
        ref = owner_ref(user["digilockerId"])
        try:
            record = await ledger.q(p.claims["username"], "ReadCitizen", ref)
        except HTTPException:
            record = None
        return {"digilockerId": user["digilockerId"], "name": user["name"], "ownerRef": ref,
                "did": record and record["did"], "registered": record is not None}

    def _boundary_from(body: dict[str, Any]) -> dict[str, Any] | None:
        raw = body.get("boundary")
        if raw in (None, "", {}):
            return None
        try:
            return geometry.parse_boundary(raw)
        except ValueError as exc:
            raise HTTPException(400, f"The boundary is invalid: {exc}")

    def _area(body: dict[str, Any]) -> float:
        try:
            area = float(str(body.get("areaSqm", "")).replace(",", ""))
        except ValueError:
            raise HTTPException(400, "Enter the area in square metres")
        if area <= 0:
            raise HTTPException(400, "Enter the area in square metres")
        return area

    @app.post("/api/parcels")
    async def register_parcel(body: dict = Body(...), p: auth.Principal = Depends(auth.officer)):
        """Register a parcel with its boundary: drawn/pasted (`boundary`), or `autoPlace` in the village layout.

        The boundary is checked against the geospatial database first; one that overlaps another parcel is refused
        unless the Registrar records the dispute (`allowOverlap` with a `note`)."""
        auth.need_role(p, "registrar")
        owner = await dl.user(str(body.get("ownerDigilockerId", "")).strip())
        village, area = str(body.get("village", "")).strip(), _area(body)
        boundary = _boundary_from(body)
        if boundary is None and body.get("autoPlace", True):
            boundary = geo.store.next_free_plot(village or "Unknown", area)
        if boundary is not None:
            check = geo.store.check_boundary(boundary, area)
            if check["overlaps"] and not body.get("allowOverlap"):
                o = check["overlaps"][0]["properties"]
                raise HTTPException(409, f"This boundary overlaps {o['surveyNo']} by {o['overlapSqm']} sq.m. Correct it, "
                                         "or record the dispute to register it anyway.")
        res = await ledger.tx(p.claims["username"], "RegisterParcel", str(body.get("surveyNo", "")).strip(),
                              village, str(body.get("district", "")), str(body.get("areaSqm", "")), owner["name"],
                              owner_ref(owner["digilockerId"]), json.dumps(boundary) if boundary else "")
        try:
            await geo.refresh(res["result"]["surveyNo"])     # don't wait for the block event: the map shows it now
        except Exception as exc:
            logger.warning("geo index refresh failed: %s", exc)
        return res

    @app.post("/api/geo/check-boundary")
    async def check_boundary(body: dict = Body(...), p: auth.Principal = Depends(auth.officer)):
        boundary = _boundary_from(body)
        if boundary is None:
            raise HTTPException(400, "Send a boundary (a GeoJSON Polygon)")
        area = _area(body) if body.get("areaSqm") not in (None, "") else None
        return {"boundary": boundary, **geo.store.check_boundary(boundary, area, exclude=body.get("surveyNo"))}

    @app.get("/api/geo/next-plot")
    async def next_plot(village: str, areaSqm: float, p: auth.Principal = Depends(auth.officer)):
        if areaSqm <= 0:
            raise HTTPException(400, "areaSqm must be positive")
        boundary = geo.store.next_free_plot(village.strip() or "Unknown", areaSqm)
        return {"boundary": boundary, **geo.store.check_boundary(boundary, areaSqm)}

    @app.post("/api/documents/issue")
    async def issue_document(body: dict = Body(...), p: auth.Principal = Depends(auth.officer)):
        """The Sub-Registrar's office issues a deed or EC into a citizen's DigiLocker."""
        auth.need_role(p, "registrar")
        kind = str(body.get("docType", "")).upper()
        holder = await dl.user(str(body.get("digilockerId", "")).strip())
        if kind == "SALEDEED":
            required = ("surveyNo", "village", "district", "extentSqm", "vendor", "purchaser", "considerationINR")
            if any(not str(body.get(k, "")).strip() for k in required):
                raise HTTPException(400, f"A sale deed needs: {', '.join(required)}")
            content, desc = deed_text(body), f"Sale deed for {body['surveyNo']}"
        elif kind == "ENCUMBRANCE":
            if not str(body.get("surveyNo", "")).strip():
                raise HTTPException(400, "An encumbrance certificate needs a survey number")
            content, desc = ec_text(body), f"Encumbrance certificate for {body['surveyNo']}"
        else:
            raise HTTPException(400, "docType must be SALEDEED or ENCUMBRANCE")
        return await dl.issue(holder["digilockerId"], kind, settings.SRO, desc, content)

    @app.get("/api/transfer-requests")
    async def transfer_requests(p: auth.Principal = Depends(auth.officer)):
        return {"intents": intents.by_status("ready", "awaiting-buyer", "filed")[:50]}

    @app.post("/api/applications")
    async def open_application(body: dict = Body(...), p: auth.Principal = Depends(auth.officer)):
        auth.need_role(p, "clerk")
        rec = intents.get(str(body.get("intentId", "")))
        if not rec or rec["status"] != "ready":
            raise HTTPException(409, "That transfer intent is not ready (both parties must sign it first)")
        i = rec["intent"]
        app_id = f"APP-{dt.datetime.now(dt.timezone.utc):%Y%m%d}-{secrets.token_hex(2).upper()}"
        consent = json.dumps({"intent": i, "seller": rec["sellerSignature"], "buyer": rec["buyerSignature"]})
        res = await ledger.tx(p.claims["username"], "SubmitTransferApplication", app_id, i["surveyNo"], i["sellerRef"],
                              rec["buyerName"], i["buyerRef"], str(i["considerationINR"]), consent)
        rec.update(status="filed", appId=app_id, filedBy=p.claims["username"], filedAt=crypto.iso(crypto.utcnow()))
        intents.save(rec)
        return {**res, "appId": app_id}

    async def _seller_dl(a: dict[str, Any], purpose: str) -> str:
        for rec in intents.for_ref(a["sellerRef"]):
            if rec.get("appId") == a["appId"]:
                return rec["sellerDigilockerId"]
        return (await dl.holder(a["sellerRef"], purpose))["digilockerId"]

    @app.get("/api/applications/{app_id}/seller-documents")
    async def seller_documents(app_id: str, p: auth.Principal = Depends(auth.officer)):
        auth.need_role(p, "clerk", "registrar")
        a = await ledger.q(p.claims["username"], "ReadApplication", app_id)
        dl_id = await _seller_dl(a, f"Listing documents for {app_id}")
        anchored = {d["docId"] for d in a["documents"]}
        docs = await dl.documents(dl_id)
        return {"documents": [{k: d[k] for k in ("docId", "docType", "orgName", "description", "issuedAt", "contentSha256")}
                              | {"anchored": d["docId"] in anchored} for d in docs]}

    @app.post("/api/applications/{app_id}/anchor")
    async def anchor_document(app_id: str, body: dict = Body(...), p: auth.Principal = Depends(auth.officer)):
        auth.need_role(p, "clerk")
        user = p.claims["username"]
        a = await ledger.q(user, "ReadApplication", app_id)
        dl_id = await _seller_dl(a, f"Anchoring documents for {app_id}")
        doc_id = str(body.get("docId", ""))
        meta = next((d for d in await dl.documents(dl_id) if d["docId"] == doc_id), None)
        if not meta:
            raise HTTPException(404, "That document is not in the seller's DigiLocker")
        doc = await dl.consent_fetch("Registration Dept (Org1)", dl_id, str(body.get("otp", "")), meta["docType"],
                                     meta["orgName"], doc_id)
        computed = hashlib.sha256(doc["content"].encode()).hexdigest()
        if computed != doc["contentSha256"]:
            raise HTTPException(409, "This document's content no longer matches the fingerprint DigiLocker recorded "
                                     "when it was issued; it cannot be anchored")
        return await ledger.tx(user, "AnchorDocument", app_id, computed, doc_id, doc["docType"], doc["orgName"])

    @app.post("/api/applications/{app_id}/forward")
    async def forward(app_id: str, p: auth.Principal = Depends(auth.officer)):
        auth.need_role(p, "registrar")
        return await ledger.tx(p.claims["username"], "ForwardToRevenue", app_id)

    @app.post("/api/applications/{app_id}/verify")
    async def verify_documents(app_id: str, body: dict = Body(...), p: auth.Principal = Depends(auth.officer)):
        """Revenue's independent check: its OWN consent request, its OWN hashing."""
        auth.need_role(p, "verifier")
        user = p.claims["username"]
        a = await ledger.q(user, "ReadApplication", app_id)
        dl_id = await _seller_dl(a, f"Revenue verification of {app_id}")
        hashes, checked = [], []
        for d in a["documents"]:
            doc = await dl.consent_fetch("Revenue Dept (Org2)", dl_id, str(body.get("otp", "")), d["documentType"],
                                         d["issuer"], d["docId"])
            h = hashlib.sha256(doc["content"].encode()).hexdigest()
            hashes.append(h)
            checked.append({"docId": d["docId"], "anchored": d["sha256"], "computed": h, "match": h == d["sha256"]})
        res = await ledger.tx(user, "VerifyDocuments", app_id, json.dumps(hashes))
        return {**res, "checked": checked}

    @app.post("/api/applications/{app_id}/survey")
    async def survey(app_id: str, body: dict = Body(...), p: auth.Principal = Depends(auth.officer)):
        auth.need_role(p, "surveyor")
        return await ledger.tx(p.claims["username"], "RecordSurvey", app_id, str(body.get("measuredAreaSqm", "")),
                               str(body.get("boundaries", "")))

    @app.post("/api/applications/{app_id}/forward-collectorate")
    async def forward_collectorate(app_id: str, p: auth.Principal = Depends(auth.officer)):
        auth.need_role(p, "verifier")
        return await ledger.tx(p.claims["username"], "ForwardToCollectorate", app_id)

    @app.post("/api/applications/{app_id}/approve")
    async def approve(app_id: str, body: dict = Body(...), p: auth.Principal = Depends(auth.officer)):
        auth.need_role(p, "collector")
        return await ledger.tx(p.claims["username"], "ApproveTransfer", app_id, str(body.get("remarks", "")))

    @app.post("/api/applications/{app_id}/reject")
    async def reject(app_id: str, body: dict = Body(...), p: auth.Principal = Depends(auth.officer)):
        auth.need_role(p, "collector")
        return await ledger.tx(p.claims["username"], "RejectApplication", app_id, str(body.get("reason", "")))

    @app.post("/api/applications/{app_id}/revalidate")
    async def revalidate(app_id: str, p: auth.Principal = Depends(auth.officer)):
        auth.need_role(p, "verifier", "collector")
        try:
            resp = await http.post(f"{agent_url}/runs", json={"appId": app_id, "requestedBy": p.claims["username"]},
                                   headers={"X-Agent-Token": settings.AGENT_TOKEN}, timeout=5)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "The validator is offline") from exc
        return resp.json()

    @app.get("/api/ledger/blocks")
    async def blocks(before: int | None = None, limit: int = 15, p: auth.Principal = Depends(auth.officer)):
        params = {"limit": limit} | ({"before": before} if before is not None else {})
        resp = await http.get(f"{config.url(config.org_peer(p.claims['mspId']))}/blocks", params=params)
        return resp.json()

    @app.get("/api/ledger/blocks/{number}")
    async def block(number: int, p: auth.Principal = Depends(auth.officer)):
        resp = await http.get(f"{config.url(config.org_peer(p.claims['mspId']))}/blocks/{number}")
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "No such block")
        return resp.json()

    # =====================================================================
    # Citizens
    # =====================================================================

    @app.get("/api/citizen/me")
    async def citizen_me(p: auth.Principal = Depends(auth.citizen)):
        ref = p.claims["ownerRef"]
        record, parcels, apps = await asyncio.gather(ledger.q("portal1", "ReadCitizen", ref),
                                                     ledger.q("portal1", "ListParcels"),
                                                     ledger.q("portal1", "ListApplications", "ALL"))
        mine = [a for a in apps if ref in (a["sellerRef"], a["buyerRef"])]
        mine.sort(key=lambda a: a["updatedAt"], reverse=True)
        for a in mine:
            a["myRole"] = "seller" if a["sellerRef"] == ref else "buyer"
        return {"profile": {**p.claims, "onChain": record}, "parcels": [x for x in parcels if x["ownerRef"] == ref],
                "applications": mine, "intents": intents.for_ref(ref)}

    @app.get("/api/citizen/documents")
    async def citizen_documents(p: auth.Principal = Depends(auth.citizen)):
        return {"documents": await dl.documents(p.claims["digilockerId"])}

    @app.get("/api/citizen/access-log")
    async def citizen_access_log(p: auth.Principal = Depends(auth.citizen)):
        return {"entries": await dl.access_log(p.claims["digilockerId"])}

    @app.post("/api/citizen/intents/prepare")
    async def prepare_intent(body: dict = Body(...), p: auth.Principal = Depends(auth.citizen)):
        survey_no = str(body.get("surveyNo", "")).strip()
        parcel = await ledger.q("portal1", "ReadParcel", survey_no)
        if parcel["ownerRef"] != p.claims["ownerRef"]:
            raise HTTPException(403, "You can only sell land registered to you")
        if parcel["status"] != "ACTIVE":
            raise HTTPException(409, f"Parcel {survey_no} already has a pending transfer")
        buyer = await dl.user(str(body.get("buyerDigilockerId", "")).strip())
        buyer_ref = owner_ref(buyer["digilockerId"])
        if buyer_ref == p.claims["ownerRef"]:
            raise HTTPException(400, "You cannot sell to yourself")
        try:
            await ledger.q("portal1", "ReadCitizen", buyer_ref)
        except HTTPException:
            raise HTTPException(409, f"{buyer['name']} has not registered a LandChain identity yet")
        amount = int(str(body.get("considerationINR", "0")).replace(",", "") or 0)
        if amount <= 0:
            raise HTTPException(400, "Enter the sale consideration in rupees")
        intent = {"type": "landchain.transfer-intent/v1", "intentId": uuid.uuid4().hex, "surveyNo": survey_no,
                  "sellerRef": p.claims["ownerRef"], "buyerRef": buyer_ref, "considerationINR": amount,
                  "createdAt": crypto.iso(crypto.utcnow())}
        return {"intent": intent, "buyerName": buyer["name"], "canonical": canonical(intent).decode()}

    @app.post("/api/citizen/intents")
    async def create_intent(body: dict = Body(...), p: auth.Principal = Depends(auth.citizen)):
        intent, sig = body.get("intent") or {}, body.get("signature", "")
        if intent.get("sellerRef") != p.claims["ownerRef"]:
            raise HTTPException(403, "Only the seller can create this intent")
        if not didkey.verify(p.claims["did"], canonical(intent), _b64d(sig)):
            raise HTTPException(400, "Your signature on the intent does not verify")
        if intents.get(str(intent.get("intentId", ""))):
            raise HTTPException(409, "This intent was already submitted")
        buyer = await dl.holder(str(intent.get("buyerRef", "")), "Preparing a transfer intent")
        rec = {"id": intent["intentId"], "status": "awaiting-buyer", "intent": intent,
               "sellerName": p.claims["name"], "buyerName": buyer["name"], "sellerDigilockerId": p.claims["digilockerId"],
               "buyerDigilockerId": buyer["digilockerId"], "sellerSignature": {"did": p.claims["did"], "signature": sig},
               "buyerSignature": None, "createdAt": crypto.iso(crypto.utcnow()), "createdTs": time.time()}
        intents.save(rec)
        return rec

    @app.get("/api/citizen/intents")
    async def my_intents(p: auth.Principal = Depends(auth.citizen)):
        return {"intents": intents.for_ref(p.claims["ownerRef"])}

    @app.post("/api/citizen/intents/{intent_id}/accept")
    async def accept_intent(intent_id: str, body: dict = Body(...), p: auth.Principal = Depends(auth.citizen)):
        rec = intents.get(intent_id)
        if not rec or rec["intent"]["buyerRef"] != p.claims["ownerRef"]:
            raise HTTPException(404, "No such intent addressed to you")
        if rec["status"] != "awaiting-buyer":
            raise HTTPException(409, f"This intent is {rec['status']}")
        if not didkey.verify(p.claims["did"], canonical(rec["intent"]), _b64d(body.get("signature", ""))):
            raise HTTPException(400, "Your signature on the intent does not verify")
        rec.update(status="ready", buyerSignature={"did": p.claims["did"], "signature": body["signature"]},
                   acceptedAt=crypto.iso(crypto.utcnow()))
        intents.save(rec)
        return rec

    @app.post("/api/citizen/intents/{intent_id}/cancel")
    async def cancel_intent(intent_id: str, p: auth.Principal = Depends(auth.citizen)):
        rec = intents.get(intent_id)
        if not rec or p.claims["ownerRef"] not in (rec["intent"]["sellerRef"], rec["intent"]["buyerRef"]):
            raise HTTPException(404, "No such intent")
        if rec["status"] in ("filed",):
            raise HTTPException(409, "Already filed with Registration")
        rec["status"] = "cancelled"
        intents.save(rec)
        return rec

    @app.get("/api/citizen/reports")
    async def citizen_reports(appId: str, p: auth.Principal = Depends(auth.citizen)):
        a = await ledger.q("portal1", "ReadApplication", appId)
        if p.claims["ownerRef"] not in (a["sellerRef"], a["buyerRef"]):
            raise HTTPException(403, "You are not a party to this application")
        runs = await _agent_runs(appId)
        out = []
        for r in runs:
            if r.get("status") == "anchored":
                full = await _get(f"{agent_url}/runs/{r['runId']}", timeout=5) or {}
                out.append({"runId": r["runId"], "verdict": r.get("verdict"), "report": r.get("report"),
                            "anchor": r.get("anchor"), "finishedAt": r.get("finishedAt"),
                            "citizenExplanation": ((full.get("explanation") or {}).get("citizen") or {}).get("text"),
                            "citizenWriter": {k: ((full.get("explanation") or {}).get("citizen") or {}).get(k)
                                              for k in ("engine", "model", "faithfulness", "drafts")},
                            "quality": (full.get("quality") or {}).get("aggregate"),
                            "verification": {"independent": ((full.get("verification") or {}).get("pre") or {}).get("passed"),
                                             "ledger": ((full.get("verification") or {}).get("post") or {}).get("passed")},
                            "hasCertificate": bool((r.get("report") or {}).get("certificateCid")),
                            "checks": [{k: c[k] for k in ("label", "status", "detail")} for c in full.get("checks", [])]})
        return {"application": {k: a[k] for k in ("appId", "surveyNo", "status", "sellerName", "buyerName")}, "reports": out}

    # =====================================================================
    # Agent (proxied) -- officials see everything; citizens see their own files' reports
    # =====================================================================

    async def _can_see_run(p: auth.Principal, run_id: str) -> None:
        if p.kind == "officer":
            return
        run = await _get(f"{agent_url}/runs/{run_id}", timeout=5)
        if not run:
            raise HTTPException(404, "No such run")
        a = await ledger.q("portal1", "ReadApplication", run["appId"])
        if p.claims.get("ownerRef") not in (a["sellerRef"], a["buyerRef"]):
            raise HTTPException(403, "You are not a party to this application")

    @app.get("/api/agent/status")
    async def agent_status(p: auth.Principal = Depends(auth.officer)):
        st = await _get(f"{agent_url}/status", timeout=5)
        if not st:
            raise HTTPException(503, "The validator is offline")
        return st

    @app.get("/api/agent/runs")
    async def agent_runs(limit: int = 50, appId: str | None = None, p: auth.Principal = Depends(auth.officer)):
        url = f"{agent_url}/runs?limit={limit}" + (f"&appId={appId}" if appId else "")
        data = await _get(url, timeout=5)
        if data is None:
            raise HTTPException(503, "The validator is offline")
        return data

    @app.get("/api/agent/runs/{run_id}")
    async def agent_run(run_id: str, p: auth.Principal = Depends(auth.officer)):
        data = await _get(f"{agent_url}/runs/{run_id}", timeout=5)
        if data is None:
            raise HTTPException(404, "No such run (or the validator is offline)")
        return data

    @app.get("/api/agent/runs/{run_id}/{file}")
    async def agent_file(run_id: str, file: str, p: auth.Principal = Depends(auth.any_principal)):
        if file not in ("report.json", "report.json.asc", "trace.json", "certificate.txt", "certificate.txt.asc"):
            raise HTTPException(404, "Unknown file")
        await _can_see_run(p, run_id)
        try:
            resp = await http.get(f"{agent_url}/runs/{run_id}/{file}", timeout=10)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "The validator is offline") from exc
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, "File not available")
        headers = {k: v for k, v in resp.headers.items() if k.lower() == "content-disposition"}
        return Response(resp.content, media_type=resp.headers.get("content-type", "application/octet-stream"),
                        headers=headers)

    @app.get("/api/agent/graph")
    async def agent_graph(p: auth.Principal = Depends(auth.officer)):
        data = await _get(f"{agent_url}/graph", timeout=5)
        if data is None:
            raise HTTPException(503, "The validator is offline")
        return data

    @app.get("/api/agent/metrics")
    async def agent_metrics(p: auth.Principal = Depends(auth.officer)):
        data = await _get(f"{agent_url}/metrics", timeout=8)
        if data is None:
            raise HTTPException(503, "The validator is offline")
        return data

    @app.post("/api/agent/runs/{run_id}/verify")
    async def agent_reverify(run_id: str, p: auth.Principal = Depends(auth.officer)):
        """Run the independent verifier again over the stored files, now."""
        try:
            resp = await http.post(f"{agent_url}/runs/{run_id}/verify", timeout=30)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "The validator is offline") from exc
        if resp.status_code != 200:
            raise HTTPException(resp.status_code, resp.json().get("detail", "Could not verify") if resp.content else "Could not verify")
        return resp.json()

    @app.get("/api/agent/knowledge")
    async def agent_knowledge(p: auth.Principal = Depends(auth.officer)):
        data = await _get(f"{agent_url}/knowledge", timeout=5)
        return data or {"sections": []}

    async def _proxy_sse(method: str, url: str, payload: dict | None = None) -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            try:
                async with http.stream(method, url, json=payload, timeout=httpx.Timeout(None, connect=3.0)) as resp:
                    if resp.status_code != 200:
                        yield f"data: {json.dumps({'type': 'error', 'error': f'validator returned {resp.status_code}'})}\n\n".encode()
                        return
                    async for chunk in resp.aiter_raw():
                        yield chunk
            except httpx.HTTPError:
                yield f"data: {json.dumps({'type': 'error', 'error': 'The validator is offline'})}\n\n".encode()
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/agent/stream")
    async def agent_stream(p: auth.Principal = Depends(auth.officer)):
        return await _proxy_sse("GET", f"{agent_url}/stream")

    @app.post("/api/agent/ask")
    async def agent_ask(body: dict = Body(...), p: auth.Principal = Depends(auth.officer)):
        return await _proxy_sse("POST", f"{agent_url}/ask",
                                {"question": str(body.get("question", ""))[:600], "runId": body.get("runId")})

    # =====================================================================
    # Live block feed
    # =====================================================================

    @app.get("/api/events")
    async def events(backlog: int = 5, p: auth.Principal = Depends(auth.any_principal)):
        """Committed blocks as they happen, starting with the last `backlog` blocks."""
        st = await _get(f"{home_peer}/status")
        start = max(0, (st or {}).get("height", 0) - max(0, min(backlog, 50))) if st else -1

        async def gen() -> AsyncIterator[dict[str, Any]]:
            try:
                async with http.stream("GET", f"{home_peer}/events", params={"start": start},
                                       timeout=httpx.Timeout(None, connect=3.0)) as resp:
                    async for _ev, data in read_sse(resp):
                        b = json.loads(data)
                        yield {"type": "block", "number": b["number"], "hash": b.get("hash"), "kind": b.get("kind"),
                               "orderer": b.get("orderer"),
                               "txs": [{"txId": t.get("txId"), "fn": t.get("fn"), "code": t.get("code"),
                                        "timestamp": t.get("timestamp"),
                                        "event": (t.get("event") or {}).get("name"),
                                        "by": (t.get("creator") or {}).get("name"),
                                        "mspId": (t.get("creator") or {}).get("mspId"),
                                        "role": (t.get("creator") or {}).get("role")} for t in b["txs"]]}
            except httpx.HTTPError:
                yield {"type": "error", "error": "lost connection to the peer"}
        return sse_response(gen())

    return app


def _b64d(text: str) -> bytes:
    import base64
    import binascii
    try:
        return base64.b64decode(text or "", validate=True)
    except (binascii.Error, ValueError):
        return b""
