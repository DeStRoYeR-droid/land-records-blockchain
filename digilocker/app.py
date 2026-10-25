"""
DigiLocker Clone — a self-contained simulation of DigiLocker's core mechanics.

Mirrors the shape of the real DigiLocker/Setu API (create request -> user
consent via OTP -> check status -> fetch document -> revoke) without
integrating with any live government service.

LandChain additions (every original endpoint is unchanged):
  * every issued document carries `contentSha256`, the SHA-256 fingerprint of
    its content, computed by the issuer at issue time. This is the value the
    Registration department anchors on the ledger.
  * GET /health for the startup scripts.
  * Persistence: the store is saved to data/digilocker/store.json after every
    change, so citizens and documents survive ./shutdown.sh + ./startup.sh
    (the ledger refers to them).
  * Citizen login: POST /users/{id}/otp/verify (same mock OTP).
  * Department access: departments handling a citizen's application may pull
    the documents filed with it, using a department key. This stands in for the
    consent the citizen gives when filing. EVERY such access is written to an
    audit log the citizen can read (GET /users/{id}/access-log).
  * Demo only: POST /admin/tamper edits a stored document's content WITHOUT
    updating its fingerprint -- the insider attack the validator must catch.

Run standalone:
    pip install fastapi uvicorn
    uvicorn app:app --reload

In LandChain it runs on port 13800 (see network/nodes.yaml).
Open http://127.0.0.1:13800/docs for the interactive Swagger UI.
"""

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="DigiLocker Clone", version="1.2")

# ---------------------------------------------------------------------------
# The "database" (persisted to a JSON file)
# ---------------------------------------------------------------------------
USERS = {}             # digilockerId -> user dict
DOCUMENTS = {}         # digilockerId -> list of document dicts (the user's locker)
CONSENT_REQUESTS = {}  # request id -> request dict
ACCESS_LOG = []        # department accesses, newest last

MOCK_OTP = "123456"  # fixed OTP for demo purposes; printed to console on each request
OWNER_REF_PREFIX = "landchain-owner:"  # ownerRef = sha256(prefix + digilockerId)

_ROOT = Path(os.environ.get("LANDCHAIN_ROOT", Path(__file__).resolve().parents[1]))
STORE = Path(os.environ.get("DIGILOCKER_STORE", _ROOT / "data" / "digilocker" / "store.json"))
_lock = threading.Lock()

# Department keys: who may pull filed documents (name:key pairs).
DEPT_KEYS = dict(
    pair.split(":", 1) for pair in os.environ.get(
        "DIGILOCKER_DEPT_KEYS",
        "Registration (Org1):registration-demo-key,Revenue (Org2):revenue-demo-key,"
        "Revenue Validator Agent (Org2):validator-demo-key").split(",") if ":" in pair)
KEY_TO_DEPT = {v: k for k, v in DEPT_KEYS.items()}
DEMO_MODE = os.environ.get("DIGILOCKER_DEMO_MODE", "1") == "1"


def _load():
    if STORE.exists():
        data = json.loads(STORE.read_text())
        USERS.update(data.get("users", {}))
        DOCUMENTS.update(data.get("documents", {}))
        CONSENT_REQUESTS.update(data.get("consentRequests", {}))
        ACCESS_LOG.extend(data.get("accessLog", []))


def _save():
    with _lock:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"users": USERS, "documents": DOCUMENTS, "consentRequests": CONSENT_REQUESTS,
                                   "accessLog": ACCESS_LOG[-5000:]}, indent=1))
        tmp.replace(STORE)  # atomic


_load()


def sha256_hex(text: str) -> str:
    """Fingerprint of a document's exact UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def owner_ref(digilocker_id: str) -> str:
    return sha256_hex(OWNER_REF_PREFIX + digilocker_id)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class SignupRequest(BaseModel):
    name: str
    mobile: str
    aadhaar_last4: str  # never store real Aadhaar numbers even in a demo


class IssueDocumentRequest(BaseModel):
    digilocker_id: str
    doc_type: str        # e.g. "SALEDEED", "ENCUMBRANCE", "PANCR"
    org_name: str        # issuing authority, e.g. "Sub-Registrar Office, Warangal"
    description: str
    content: str         # mock document content/body


class CreateConsentRequest(BaseModel):
    requester_name: str
    redirect_url: str


class VerifyOtpRequest(BaseModel):
    digilocker_id: str
    otp: str


class FetchDocumentRequest(BaseModel):
    doc_type: str
    org_name: str
    consent: str  # "Y" required
    doc_id: str | None = None  # LandChain: pick this exact document when a locker holds several of a type


class OtpBody(BaseModel):
    otp: str


class TamperRequest(BaseModel):
    doc_id: str
    find: str
    replace: str


# ---------------------------------------------------------------------------
# 0. Health check
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "users": len(USERS), "consentRequests": len(CONSENT_REQUESTS),
            "documents": sum(len(d) for d in DOCUMENTS.values())}


# ---------------------------------------------------------------------------
# 1. User signup (creates a "DigiLocker account")
# ---------------------------------------------------------------------------
@app.post("/users/signup")
def signup(req: SignupRequest):
    digilocker_id = f"DL-{uuid.uuid4().hex[:12]}"
    USERS[digilocker_id] = {
        "digilockerId": digilocker_id,
        "name": req.name,
        "mobile": req.mobile,
        "aadhaarLast4": req.aadhaar_last4,
    }
    DOCUMENTS[digilocker_id] = []
    _save()
    return USERS[digilocker_id]


@app.get("/users/{digilocker_id}")
def get_user(digilocker_id: str):
    if digilocker_id not in USERS:
        raise HTTPException(404, "User not found")
    return {**USERS[digilocker_id], "ownerRef": owner_ref(digilocker_id)}


@app.post("/users/{digilocker_id}/otp/verify")
def verify_login_otp(digilocker_id: str, body: OtpBody):
    """Citizen login: the same mock OTP the consent flow uses."""
    if digilocker_id not in USERS:
        raise HTTPException(404, "User not found")
    print(f"[MOCK SMS] login OTP for {digilocker_id}: {MOCK_OTP}")
    if body.otp != MOCK_OTP:
        raise HTTPException(400, "Invalid OTP")
    return {"verified": True, "digilockerId": digilocker_id, "ownerRef": owner_ref(digilocker_id)}


# ---------------------------------------------------------------------------
# 2. Issuer uploads a document into a user's locker
# ---------------------------------------------------------------------------
@app.post("/issuer/documents")
def issue_document(req: IssueDocumentRequest):
    if req.digilocker_id not in USERS:
        raise HTTPException(404, "User not found")
    doc = {
        "docId": f"DOC-{uuid.uuid4().hex[:8]}",
        "docType": req.doc_type,
        "orgName": req.org_name,
        "description": req.description,
        "content": req.content,
        "contentSha256": sha256_hex(req.content),
        "issuedAt": datetime.utcnow().isoformat(),
    }
    DOCUMENTS[req.digilocker_id].append(doc)
    _save()
    return doc


@app.get("/users/{digilocker_id}/documents")
def list_documents(digilocker_id: str):
    if digilocker_id not in USERS:
        raise HTTPException(404, "User not found")
    return DOCUMENTS[digilocker_id]


@app.get("/users/{digilocker_id}/access-log")
def user_access_log(digilocker_id: str):
    """Every time a department pulled one of this citizen's documents."""
    if digilocker_id not in USERS:
        raise HTTPException(404, "User not found")
    return [e for e in ACCESS_LOG if e.get("holder") == digilocker_id][::-1]


# ---------------------------------------------------------------------------
# 3. Requester creates a consent request (mirrors "Create a DigiLocker request")
# ---------------------------------------------------------------------------
@app.post("/consent/request")
def create_consent_request(req: CreateConsentRequest):
    request_id = str(uuid.uuid4())
    CONSENT_REQUESTS[request_id] = {
        "id": request_id,
        "requesterName": req.requester_name,
        "redirectUrl": req.redirect_url,
        "status": "unauthenticated",
        "digilockerId": None,
        "consentedDocs": [],
        "validUpto": (datetime.utcnow() + timedelta(minutes=15)).isoformat(),
    }
    _save()
    return CONSENT_REQUESTS[request_id]


# ---------------------------------------------------------------------------
# 4. User authenticates via mock OTP to grant consent
# ---------------------------------------------------------------------------
@app.post("/consent/{request_id}/verify-otp")
def verify_otp(request_id: str, body: VerifyOtpRequest):
    req = CONSENT_REQUESTS.get(request_id)
    if not req:
        raise HTTPException(404, "Consent request not found or expired")
    if body.digilocker_id not in USERS:
        raise HTTPException(404, "User not found")

    print(f"[MOCK SMS] OTP for {body.digilocker_id}: {MOCK_OTP}")  # simulated OTP send

    if body.otp != MOCK_OTP:
        raise HTTPException(400, "Invalid OTP")

    req["status"] = "authenticated"
    req["digilockerId"] = body.digilocker_id
    # Real DigiLocker returns the account holder's verified name with consent.
    user = USERS[body.digilocker_id]
    req["user"] = {"name": user["name"], "aadhaarLast4": user["aadhaarLast4"]}
    _save()
    return req


# ---------------------------------------------------------------------------
# 5. Check request status (mirrors "Get DigiLocker request status")
# ---------------------------------------------------------------------------
@app.get("/consent/{request_id}/status")
def get_status(request_id: str):
    req = CONSENT_REQUESTS.get(request_id)
    if not req:
        raise HTTPException(404, "Consent request not found or expired")
    return req


# ---------------------------------------------------------------------------
# 6. Fetch a document once consent is granted (mirrors "Fetch a document")
# ---------------------------------------------------------------------------
@app.post("/consent/{request_id}/document")
def fetch_document(request_id: str, body: FetchDocumentRequest):
    req = CONSENT_REQUESTS.get(request_id)
    if not req:
        raise HTTPException(404, "Consent request not found or expired")
    if req["status"] != "authenticated":
        raise HTTPException(400, "User consent not available yet")
    if body.consent != "Y":
        raise HTTPException(400, "Consent flag must be 'Y' to fetch")

    user_docs = DOCUMENTS.get(req["digilockerId"], [])
    match = next(
        (d for d in user_docs if d["docType"] == body.doc_type and d["orgName"] == body.org_name
         and (body.doc_id is None or d["docId"] == body.doc_id)),
        None,
    )
    if not match:
        raise HTTPException(404, "Requested document not found in user's locker")

    req["consentedDocs"].append(match["docId"])
    _save()
    return match


# ---------------------------------------------------------------------------
# 7. Revoke a consent request
# ---------------------------------------------------------------------------
@app.get("/consent/{request_id}/revoke")
def revoke(request_id: str):
    req = CONSENT_REQUESTS.get(request_id)
    if not req:
        raise HTTPException(404, "Consent request not found or expired")
    req["status"] = "revoked"
    _save()
    return {"success": True}


# ---------------------------------------------------------------------------
# 8. Department access (LandChain addition) -- key-gated and audited
# ---------------------------------------------------------------------------
def _department(key: str | None) -> str:
    dept = KEY_TO_DEPT.get(key or "")
    if not dept:
        raise HTTPException(401, "a valid X-Department-Key is required")
    return dept


def _audit(dept: str, action: str, holder: str | None, doc_id: str | None, purpose: str | None):
    ACCESS_LOG.append({"at": datetime.utcnow().isoformat() + "Z", "department": dept, "action": action,
                       "holder": holder, "docId": doc_id, "purpose": purpose or ""})
    _save()


@app.get("/department/documents/{doc_id}")
def department_document(doc_id: str, purpose: str = "", x_department_key: str | None = Header(None)):
    """A department pulls a document filed with an application it is handling."""
    dept = _department(x_department_key)
    for holder, docs in DOCUMENTS.items():
        for d in docs:
            if d["docId"] == doc_id:
                _audit(dept, "document", holder, doc_id, purpose)
                user = USERS[holder]
                return {**d, "holder": {"digilockerId": holder, "name": user["name"],
                                        "ownerRef": owner_ref(holder)}}
    raise HTTPException(404, "No such document")


@app.get("/department/holders/{owner_ref_hex}")
def department_holder(owner_ref_hex: str, purpose: str = "", x_department_key: str | None = Header(None)):
    """Resolve an on-ledger ownerRef to the DigiLocker account holder's verified name."""
    dept = _department(x_department_key)
    for dl_id, user in USERS.items():
        if owner_ref(dl_id) == owner_ref_hex:
            _audit(dept, "identity", dl_id, None, purpose)
            return {"digilockerId": dl_id, "name": user["name"], "aadhaarLast4": user["aadhaarLast4"],
                    "ownerRef": owner_ref_hex}
    raise HTTPException(404, "No DigiLocker account has this ownerRef")


@app.post("/admin/tamper")
def tamper(req: TamperRequest):
    """DEMO ONLY: alter a stored document's content but keep its recorded fingerprint."""
    if not DEMO_MODE:
        raise HTTPException(403, "demo mode is off")
    for docs in DOCUMENTS.values():
        for d in docs:
            if d["docId"] == req.doc_id:
                if req.find not in d["content"]:
                    raise HTTPException(400, "text to replace not found")
                d["content"] = d["content"].replace(req.find, req.replace)
                _save()
                print(f"[DEMO] document {req.doc_id} altered at source; contentSha256 left unchanged")
                return {"docId": d["docId"], "contentSha256": d["contentSha256"],
                        "actualSha256": sha256_hex(d["content"])}
    raise HTTPException(404, "No such document")


@app.get("/")
def root():
    return {"message": "DigiLocker Clone API — visit /docs for the interactive UI"}
