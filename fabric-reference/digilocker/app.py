"""
DigiLocker Clone — a self-contained simulation of DigiLocker's core mechanics.

Mirrors the shape of the real DigiLocker/Setu API (create request -> user
consent via OTP -> check status -> fetch document -> revoke) without
integrating with any live government service. In-memory store — fine for a
course demo; swap for SQLite/Postgres if you want persistence.

LandChain additions (everything else is unchanged):
  * every issued document carries `contentSha256`, the SHA-256 fingerprint of
    its content, computed by the issuer at issue time. This is the value the
    Registration department anchors on the ledger.
  * GET /health for the startup scripts.

Run standalone:
    pip install fastapi uvicorn
    uvicorn app:app --reload

In LandChain it runs as the `digilocker` container on port 8000.
Open http://127.0.0.1:8000/docs for the interactive Swagger UI.
"""

import hashlib
import uuid
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="DigiLocker Clone", version="1.1")

# ---------------------------------------------------------------------------
# In-memory "database"
# ---------------------------------------------------------------------------
USERS = {}             # digilockerId -> user dict
DOCUMENTS = {}         # digilockerId -> list of document dicts (the user's locker)
CONSENT_REQUESTS = {}  # request id -> request dict

MOCK_OTP = "123456"  # fixed OTP for demo purposes; printed to console on each request


def sha256_hex(text: str) -> str:
    """Fingerprint of a document's exact UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


# ---------------------------------------------------------------------------
# 0. Health check
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "users": len(USERS), "consentRequests": len(CONSENT_REQUESTS)}


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
    return USERS[digilocker_id]


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
    return doc


@app.get("/users/{digilocker_id}/documents")
def list_documents(digilocker_id: str):
    if digilocker_id not in USERS:
        raise HTTPException(404, "User not found")
    return DOCUMENTS[digilocker_id]


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
        (d for d in user_docs if d["docType"] == body.doc_type and d["orgName"] == body.org_name),
        None,
    )
    if not match:
        raise HTTPException(404, "Requested document not found in user's locker")

    req["consentedDocs"].append(match["docId"])
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
    return {"success": True}


@app.get("/")
def root():
    return {"message": "DigiLocker Clone API — visit /docs for the interactive UI"}
