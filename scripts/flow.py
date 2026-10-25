"""
flow.py -- a transfer file driven through the gateway's REST API, for the check scripts.

Does what the citizen and officer dashboards do: two citizens register with
fresh did:key identities, the registrar registers a parcel and issues a deed
and an encumbrance certificate, seller and buyer sign a transfer intent, the
clerk opens the file and anchors the documents, and the registrar hands it to
Revenue (which wakes the validator).
"""

from __future__ import annotations

import base64
import secrets
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from chaincode.landrec import didkey  # noqa: E402
from chaincode.landrec.landrec import canonical  # noqa: E402
from nodes.common import config  # noqa: E402

GW = config.url("gateway")
OTP = "123456"
http = httpx.Client(base_url=GW, timeout=60, trust_env=False)


def call(method: str, path: str, token: str | None = None, expect: int = 200, **kw) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = http.request(method, path, headers=headers, **kw)
    if resp.status_code != expect:
        raise RuntimeError(f"{method} {path}: expected HTTP {expect}, got {resp.status_code}: {resp.text[:300]}")
    return resp.json() if "json" in resp.headers.get("content-type", "") else resp


class Browser:
    """A citizen's browser: a did:key and the signatures made with it."""

    def __init__(self, name: str, mobile: str, last4: str):
        self.name, self.mobile, self.last4 = name, mobile, last4
        self.key = Ed25519PrivateKey.generate()
        self.did = didkey.did_from_public_key(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))
        self.token = self.dl_id = ""

    def sign(self, obj: dict) -> str:
        return base64.b64encode(self.key.sign(canonical(obj))).decode()

    def register(self) -> None:
        ch = call("POST", "/api/citizen/challenge", json={"purpose": "register", "did": self.did})["challenge"]
        res = call("POST", "/api/citizen/register", json={
            "name": self.name, "mobile": self.mobile, "aadhaarLast4": self.last4, "did": self.did, "challenge": ch,
            "signature": self.sign({"type": "landchain.register/v1", "did": self.did, "challenge": ch})})
        self.dl_id, self.token = res["citizen"]["digilockerId"], res["token"]


def officers() -> dict[str, str]:
    return {u: call("POST", "/api/auth/officer", json={"username": u, "password": u})["token"]
            for u in ("registrar1", "clerk1", "verifier1", "surveyor1", "collector1")}


def open_file(deed_extent_factor: float = 1.0, consideration: int = 3850000, forward: bool = True,
              tamper: tuple[str, str] | None = None, tokens: dict[str, str] | None = None,
              anchor: bool = True, overlap_with: str | None = None, village: str = "Kazipet") -> dict[str, Any]:
    """Create a fresh transfer file and (by default) hand it to Revenue. `tamper` = (find, replace) applied to the
    deed inside DigiLocker after it was anchored (the gateway refuses to anchor an altered document), as a hostile insider would."""
    tag = uuid.uuid4().hex[:4].upper()
    tokens = tokens or officers()
    seller = Browser(f"Sunitha Reddy {tag}", f"98{secrets.randbelow(10**8):08d}", "4821")
    buyer = Browser(f"Arjun Rao {tag}", f"99{secrets.randbelow(10**8):08d}", "7710")
    seller.register()
    buyer.register()
    survey_no, area = f"SY-{tag}-12/4A", 1450
    body: dict[str, Any] = {"surveyNo": survey_no, "village": village, "district": "Hanamkonda", "areaSqm": area,
                            "ownerDigilockerId": seller.dl_id}
    if overlap_with:                       # the same ground registered twice (the Registrar records a dispute)
        ring = call("GET", "/api/public/geo/parcel", params={"surveyNo": overlap_with})["parcel"]["geometry"]["coordinates"][0]
        dx, dy = (ring[1][0] - ring[0][0]) * 0.3, (ring[1][1] - ring[0][1]) * 0.3
        shifted = [[x + dx, y + dy] for x, y in ring]
        from chaincode.landrec import geometry
        area = round(geometry.area_sqm(shifted))
        body.update(areaSqm=area, boundary={"type": "Polygon", "coordinates": [shifted]}, allowOverlap=True,
                    note="boundary disputed")
    call("POST", "/api/parcels", tokens["registrar1"], json=body)
    deed = call("POST", "/api/documents/issue", tokens["registrar1"], json={
        "docType": "SALEDEED", "digilockerId": seller.dl_id, "surveyNo": survey_no, "village": "Kazipet",
        "district": "Hanamkonda", "extentSqm": round(area * deed_extent_factor), "vendor": seller.name,
        "purchaser": buyer.name, "considerationINR": consideration})
    ec = call("POST", "/api/documents/issue", tokens["registrar1"], json={
        "docType": "ENCUMBRANCE", "digilockerId": seller.dl_id, "surveyNo": survey_no})
    intent = call("POST", "/api/citizen/intents/prepare", seller.token, json={
        "surveyNo": survey_no, "buyerDigilockerId": buyer.dl_id, "considerationINR": str(consideration)})["intent"]
    call("POST", "/api/citizen/intents", seller.token, json={"intent": intent, "signature": seller.sign(intent)})
    call("POST", f"/api/citizen/intents/{intent['intentId']}/accept", buyer.token, json={"signature": buyer.sign(intent)})
    app_id = call("POST", "/api/applications", tokens["clerk1"], json={"intentId": intent["intentId"]})["appId"]
    for d in (call("GET", f"/api/applications/{app_id}/seller-documents", tokens["clerk1"])["documents"] if anchor else []):
        if d["docType"] in ("SALEDEED", "ENCUMBRANCE") and survey_no in d["description"]:
            call("POST", f"/api/applications/{app_id}/anchor", tokens["clerk1"], json={"docId": d["docId"], "otp": OTP})
    if tamper:
        r = httpx.post(f"{config.url('digilocker')}/admin/tamper", trust_env=False,
                       json={"doc_id": deed["docId"], "find": tamper[0], "replace": tamper[1]})
        r.raise_for_status()
    if forward:
        call("POST", f"/api/applications/{app_id}/forward", tokens["registrar1"])
    return {"appId": app_id, "surveyNo": survey_no, "deedId": deed["docId"], "ecId": ec["docId"], "seller": seller,
            "buyer": buyer, "tokens": tokens}
