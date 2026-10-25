"""
The validator's tools, and the trace that records every use of them.

Tools are the agent's only way to learn anything. Each call goes through
Trace.tool(), which records what was called, WHY (the reason the policy gave),
what came back (summarised) and how long it took. That trace is what the agent
dashboard streams live and what gets stored, content-addressed, next to the
report -- so "why did the validator say this?" always has an answer.

Ledger tools use the agent's own certificate (agent1, Org2MSP, role=agent)
for read-only queries. DigiLocker tools use the department key of the
"Revenue Validator Agent (Org2)"; every pull lands in the citizen's access log.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from nodes.common import crypto
from nodes.sdk import ChaincodeError, Gateway

from . import settings

Publish = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class Step:
    seq: int
    kind: str          # tool | decision | check | llm | measure | verify | sign | store | anchor
    name: str
    label: str
    reason: str
    status: str = "running"   # running | ok | warn | fail | error | info
    input: dict[str, Any] = field(default_factory=dict)
    output: Any = None
    startedAt: str = ""
    ms: int = 0


class Trace:
    def __init__(self, run_id: str, publish: Publish):
        self.run_id = run_id
        self.publish = publish
        self.steps: list[Step] = []

    @classmethod
    def restore(cls, run_id: str, publish: Publish, steps: list[dict[str, Any]]) -> "Trace":
        """Rebuild a trace from its saved steps (a run resumed after a restart)."""
        trace = cls(run_id, publish)
        trace.steps = [Step(**s) for s in steps]
        return trace

    async def _emit(self, step: Step) -> None:
        await self.publish({"type": "step", "runId": self.run_id, "step": asdict(step)})

    async def tool(self, name: str, label: str, reason: str, fn: Callable[[], Awaitable[Any]],
                   inputs: dict[str, Any] | None = None, summarise: Callable[[Any], Any] | None = None) -> Any:
        step = Step(len(self.steps) + 1, "tool", name, label, reason, input=inputs or {},
                    startedAt=crypto.iso(crypto.utcnow()))
        self.steps.append(step)
        await self._emit(step)
        t0 = time.monotonic()
        try:
            result = await fn()
            step.status = "ok"
            step.output = summarise(result) if summarise else result
            return result
        except Exception as exc:
            step.status = "error"
            step.output = {"error": str(exc)[:300]}
            raise
        finally:
            step.ms = int((time.monotonic() - t0) * 1000)
            await self._emit(step)

    async def note(self, kind: str, name: str, label: str, reason: str, status: str = "info",
                   output: Any = None, ms: int = 0) -> Step:
        step = Step(len(self.steps) + 1, kind, name, label, reason, status=status, output=output,
                    startedAt=crypto.iso(crypto.utcnow()), ms=ms)
        self.steps.append(step)
        await self._emit(step)
        return step

    def as_list(self) -> list[dict[str, Any]]:
        return [asdict(s) for s in self.steps]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Tools:
    def __init__(self, gateway: Gateway, http: httpx.AsyncClient, trace: Trace):
        self.gw = gateway
        self.http = http
        self.trace = trace

    # -- ledger (read-only queries with the agent's certificate) --------------

    async def _q(self, fn: str, *args: str) -> Any:
        return json.loads(await self.gw.evaluate("landrec", fn, *args))

    async def read_application(self, app_id: str, reason: str) -> dict[str, Any]:
        return await self.trace.tool(
            "ledger.ReadApplication", f"Read application {app_id} from the ledger", reason,
            lambda: self._q("ReadApplication", app_id), {"appId": app_id},
            lambda a: {"status": a["status"], "surveyNo": a["surveyNo"], "documents": len(a["documents"]),
                       "seller": a["sellerName"], "buyer": a["buyerName"], "considerationINR": a["considerationINR"]})

    async def read_parcel(self, survey_no: str, reason: str) -> dict[str, Any]:
        return await self.trace.tool(
            "ledger.ReadParcel", f"Read parcel {survey_no}", reason,
            lambda: self._q("ReadParcel", survey_no), {"surveyNo": survey_no},
            lambda p: {k: p.get(k) for k in ("ownerName", "areaSqm", "village", "district", "status", "lastTransfer")})

    async def parcel_history(self, survey_no: str, reason: str) -> list[dict[str, Any]]:
        return await self.trace.tool(
            "ledger.ParcelHistory", f"Ownership history of {survey_no}", reason,
            lambda: self._q("ParcelHistory", survey_no), {"surveyNo": survey_no},
            lambda h: {"versions": len(h), "owners": list(dict.fromkeys(
                (v.get("value") or {}).get("ownerName") for v in h if v.get("value")))})

    async def list_applications(self, reason: str) -> list[dict[str, Any]]:
        return await self.trace.tool(
            "ledger.ListApplications", "Scan every application on the ledger", reason,
            lambda: self._q("ListApplications", "ALL"), {"status": "ALL"},
            lambda apps: {"applications": len(apps), "byStatus": _count(a["status"] for a in apps)})

    async def list_parcels(self, reason: str) -> list[dict[str, Any]]:
        return await self.trace.tool(
            "ledger.ListParcels", "Scan every parcel on the ledger", reason,
            lambda: self._q("ListParcels"), {},
            lambda ps: {"parcels": len(ps)})

    async def verify_document(self, sha: str, reason: str) -> dict[str, Any]:
        return await self.trace.tool(
            "ledger.VerifyDocument", f"Is fingerprint {sha[:12]}... anchored?", reason,
            lambda: self._q("VerifyDocument", sha), {"sha256": sha},
            lambda r: {"anchored": r.get("anchored"), "appId": r.get("appId"), "docId": r.get("docId")})

    async def read_citizen(self, owner_ref: str, label: str, reason: str) -> dict[str, Any] | None:
        async def run():
            try:
                return await self._q("ReadCitizen", owner_ref)
            except ChaincodeError:
                return None
        return await self.trace.tool(
            "ledger.ReadCitizen", f"Registered DID of the {label}", reason, run, {"ownerRef": owner_ref[:16] + "..."},
            lambda c: {"did": c["did"][:32] + "...", "registeredBy": c["registeredBy"]} if c else {"registered": False})

    async def ownership_chain(self, survey_no: str, reason: str) -> dict[str, Any]:
        return await self.trace.tool(
            "ledger.OwnershipChain", f"Chain of ownership of {survey_no}", reason,
            lambda: self._q("OwnershipChain", survey_no), {"surveyNo": survey_no},
            lambda c: {"owners": c["owners"], "acquired": [l["acquired"] for l in c["links"]]})

    # -- the geospatial database (public endpoints of the gateway) ----------------

    async def _geo(self, path: str, params: dict[str, Any]) -> Any:
        resp = await self.http.get(f"{settings.GATEWAY_URL}/api/public/geo/{path}", params=params, timeout=10)
        if resp.status_code == 404:
            raise LookupError(resp.json().get("error", "not found"))
        resp.raise_for_status()
        return resp.json()

    async def geo_overlaps(self, survey_no: str, reason: str) -> dict[str, Any]:
        return await self.trace.tool(
            "geo.overlaps", f"Boundaries overlapping {survey_no}", reason,
            lambda: self._geo("overlaps", {"surveyNo": survey_no}), {"surveyNo": survey_no},
            lambda r: {"overlaps": [(f["id"], f["properties"]["overlapSqm"]) for f in r["features"]]})

    async def geo_neighbours(self, survey_no: str, within_m: float, reason: str) -> dict[str, Any]:
        return await self.trace.tool(
            "geo.neighbours", f"Parcels within {within_m:g} m of {survey_no}", reason,
            lambda: self._geo("parcel", {"surveyNo": survey_no, "within": within_m}), {"surveyNo": survey_no, "withinM": within_m},
            lambda r: {"neighbours": [f["id"] for f in r["neighbours"]["features"]]})

    async def geo_near(self, lng: float, lat: float, radius_m: float, reason: str) -> dict[str, Any]:
        return await self.trace.tool(
            "geo.near", f"Parcels within {radius_m:g} m of {lat:.5f} N, {lng:.5f} E", reason,
            lambda: self._geo("near", {"lng": lng, "lat": lat, "radius": radius_m, "limit": 60}),
            {"lng": lng, "lat": lat, "radiusM": radius_m},
            lambda r: {"parcels": len(r["features"])})

    # -- DigiLocker (department pull; audited for the citizen) --------------------

    async def _dl(self, path: str, purpose: str) -> dict[str, Any]:
        resp = await self.http.get(f"{settings.DIGILOCKER_URL}{path}", params={"purpose": purpose},
                                   headers={"X-Department-Key": settings.DIGILOCKER_KEY}, timeout=10)
        if resp.status_code == 404:
            raise LookupError(resp.json().get("detail", "not found"))
        resp.raise_for_status()
        return resp.json()

    async def fetch_document(self, doc_id: str, purpose: str, reason: str, label: str | None = None) -> dict[str, Any]:
        def summary(d: dict[str, Any]) -> dict[str, Any]:
            return {"docType": d["docType"], "issuer": d["orgName"], "bytes": len(d["content"].encode()),
                    "issuerFingerprint": d["contentSha256"][:16] + "...",
                    "computedFingerprint": sha256_text(d["content"])[:16] + "...",
                    "holder": d["holder"]["name"]}
        return await self.trace.tool(
            "digilocker.fetchDocument", label or f"Pull {doc_id} from DigiLocker", reason,
            lambda: self._dl(f"/department/documents/{doc_id}", purpose), {"docId": doc_id, "purpose": purpose}, summary)

    async def resolve_holder(self, owner_ref: str, party: str, purpose: str, reason: str) -> dict[str, Any] | None:
        async def run():
            try:
                return await self._dl(f"/department/holders/{owner_ref}", purpose)
            except LookupError:
                return None
        return await self.trace.tool(
            "digilocker.resolveHolder", f"Verified name of the {party} (DigiLocker)", reason, run,
            {"ownerRef": owner_ref[:16] + "..."},
            lambda h: {"name": h["name"], "aadhaarLast4": h["aadhaarLast4"]} if h else {"found": False})


def _count(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out
