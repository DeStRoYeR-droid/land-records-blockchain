"""
LandRegistry -- smart contract for the LandChain network.

A line-for-line Python port of fabric-reference/chaincode/landrec/lib/landRegistry.js
(same function names, arguments, error messages and application lifecycle),
plus three additions:

  * Citizens hold their own keys (did:key). Opening a transfer needs the
    SELLER's and the BUYER's signatures over the same transfer intent, and the
    contract verifies both. A clerk can no longer open a transfer of someone's
    land without them.
  * The Agentic AI Validator (Org2, role=agent) can anchor the SHA-256 of its
    signed validation report to an application. It cannot do anything else:
    every decision function still requires a human officer's role.
  * The Revenue department attests the validator's PGP key on the ledger, so
    anyone can check a report was signed by the attested key.
  * A parcel carries its surveyed boundary (a GeoJSON polygon). The contract
    validates it and refuses a boundary whose area disagrees with the recorded
    area. The geospatial database (geo/) is built from these ledger records.
  * OwnershipChain() derives the chain of owners of a parcel from its history,
    and the Collectorate attests the Records Office key that signs the
    downloadable transfer records.

Three departments share one ledger:
  Org1MSP  Registration   registers citizens/parcels, opens transfer files, anchors documents
  Org2MSP  Revenue        verifies documents, surveys the parcel; hosts the validator agent
  Org3MSP  Collectorate   final human decision (approve / reject)

Access control reads the caller's X.509 certificate on every transaction: the
MSP ID (which department issued it) and the `role` attribute the department's
CA embedded in it. Nothing about the caller is taken from the arguments.

Determinism: every endorsing peer must compute identical results, so there is
no clock, no randomness and no I/O here; times come from the transaction
timestamp chosen by the client.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import math
import re
from typing import Any

from nodes.shim import Context, Contract

from . import didkey, geometry

REGISTRATION = "Org1MSP"
REVENUE = "Org2MSP"
COLLECTORATE = "Org3MSP"


class STATUS:
    SUBMITTED = "SUBMITTED"                  # Org1 clerk opened the file
    WITH_REVENUE = "WITH_REVENUE"            # Org1 -> Org2 handoff
    DOCS_VERIFIED = "DOCS_VERIFIED"          # Org2 fingerprints matched
    FLAGGED = "FLAGGED"                      # Org2 fingerprints did NOT match
    SURVEYED = "SURVEYED"                    # Org2 field survey recorded
    WITH_COLLECTORATE = "WITH_COLLECTORATE"  # Org2 -> Org3 handoff
    APPROVED = "APPROVED"                    # Org3 approved; ownership moved
    REJECTED = "REJECTED"                    # Org3 rejected; parcel released


SURVEY_TOLERANCE_PCT = 5
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^[0-9A-F]{40}$")
CID_RE = re.compile(r"^b[a-z2-7]{20,}$")
INTENT_TYPE = "landchain.transfer-intent/v1"
INTENT_MAX_AGE = dt.timedelta(days=30)
VERDICTS = ("clear", "attention", "flagged")
# The validator may attach evidence while the file is with Revenue or the Collectorate,
# never after the human decision.
REPORTABLE = (STATUS.WITH_REVENUE, STATUS.DOCS_VERIFIED, STATUS.FLAGGED, STATUS.SURVEYED,
              STATUS.WITH_COLLECTORATE)


def canonical(obj: Any) -> bytes:
    """The exact bytes citizens sign (the web app produces the same with sorted-key JSON)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _num(value: float) -> float | int:
    """JSON-friendly number: 2400.0 -> 2400 (as JavaScript would print it)."""
    return int(value) if float(value).is_integer() else value


def _js_round_2dp(x: float) -> float:
    """Math.round(x * 100) / 100 with JavaScript's round-half-up behaviour."""
    return math.floor(x * 100 + 0.5) / 100


class LandRegistry(Contract):
    name = "LandRegistry"

    # =====================================================================
    # Identity helpers
    # =====================================================================

    def _caller(self, ctx: Context) -> dict[str, Any]:
        cid = ctx.client_identity
        x509_id = cid.get_id()
        cn = re.search(r"CN=([^,/:]+)", x509_id)
        return {
            "mspId": cid.get_msp_id(),
            "name": cn.group(1) if cn else x509_id,
            "role": cid.get_attribute_value("role") or None,
            "dept": cid.get_attribute_value("dept") or None,
        }

    def _require(self, ctx: Context, action: str, msp_id: str, roles: list[str]) -> dict[str, Any]:
        c = self._caller(ctx)
        if c["mspId"] != msp_id or c["role"] not in roles:
            raise Exception(
                f"ACCESS DENIED: {action} requires an {msp_id} certificate with role="
                f"{'|'.join(roles)}; caller {c['name']} holds {c['mspId']} role={c['role'] or 'none'}")
        return c

    # =====================================================================
    # Ledger helpers
    # =====================================================================

    def _now(self, ctx: Context) -> str:
        ts = ctx.stub.get_tx_timestamp().astimezone(dt.timezone.utc)
        return ts.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def _key(self, ctx: Context, doc_type: str, id_: str) -> str:
        return ctx.stub.create_composite_key(doc_type, [id_])

    def _get(self, ctx: Context, doc_type: str, id_: str) -> dict[str, Any] | None:
        data = ctx.stub.get_state(self._key(ctx, doc_type, id_))
        if not data:
            return None
        return json.loads(data.decode("utf-8"))

    def _must_get(self, ctx: Context, doc_type: str, id_: str, label: str) -> dict[str, Any]:
        obj = self._get(ctx, doc_type, id_)
        if not obj:
            raise Exception(f"{label} {id_} does not exist")
        return obj

    def _put(self, ctx: Context, doc_type: str, id_: str, obj: dict[str, Any]) -> None:
        ctx.stub.put_state(self._key(ctx, doc_type, id_), json.dumps(obj).encode("utf-8"))

    def _history(self, ctx: Context, doc_type: str, id_: str) -> list[dict[str, Any]]:
        out = []
        for km in ctx.stub.get_history_for_key(self._key(ctx, doc_type, id_)):
            out.append({
                "txId": km.tx_id,
                "timestamp": km.timestamp,
                "isDelete": km.is_delete,
                "value": json.loads(km.value.decode("utf-8")) if km.value else None,
            })
        return out

    def _scan(self, ctx: Context, doc_type: str) -> list[dict[str, Any]]:
        return [json.loads(v.decode("utf-8")) for _k, v in ctx.stub.get_state_by_partial_composite_key(doc_type, [])]

    def _must_status(self, app: dict[str, Any], allowed: list[str] | tuple[str, ...]) -> None:
        if app["status"] not in allowed:
            raise Exception(f"Application {app['appId']} is {app['status']}; this step needs {' or '.join(allowed)}")

    def _trail(self, ctx: Context, app: dict[str, Any], caller: dict[str, Any], action: str) -> None:
        app["updatedAt"] = self._now(ctx)
        app["trail"].append({
            "at": app["updatedAt"],
            "by": caller["name"],
            "org": caller["mspId"],
            "role": caller["role"],
            "action": action,
            "status": app["status"],
            "txId": ctx.stub.get_tx_id(),
        })

    def _emit(self, ctx: Context, name: str, payload: dict[str, Any]) -> None:
        ctx.stub.set_event(name, json.dumps(payload).encode("utf-8"))

    def _require_text(self, value: Any, field: str) -> str:
        if not isinstance(value, str) or value.strip() == "":
            raise Exception(f"{field} is required")
        return value.strip()

    def _require_number(self, value: Any, field: str, minimum: float = 0, allow_zero: bool = True) -> float | int:
        text = str(value).strip()
        try:
            n = float(text) if text != "" else 0.0  # Number("") is 0 in JavaScript
        except ValueError:
            n = math.nan
        if not math.isfinite(n) or n < minimum or (not allow_zero and n == 0):
            kind = "non-negative" if allow_zero else "positive"
            raise Exception(f'{field} must be a {kind} number, got "{value}"')
        return _num(n)

    # =====================================================================
    # Identity (read-only)
    # =====================================================================

    def WhoAmI(self, ctx: Context) -> str:
        """Shows exactly what the chaincode reads from the caller's X.509 certificate."""
        c = self._caller(ctx)
        return json.dumps({**c, "x509Id": ctx.client_identity.get_id()})

    # =====================================================================
    # Citizens (new): self-sovereign keys, bound to a DigiLocker identity by Org1
    # =====================================================================

    def RegisterCitizen(self, ctx: Context, owner_ref: str, did: str) -> str:
        """Registration binds a citizen's did:key to their ownerRef after DigiLocker KYC."""
        c = self._require(ctx, "RegisterCitizen", REGISTRATION, ["portal", "clerk", "registrar"])
        owner_ref = self._require_text(owner_ref, "ownerRef")
        did = self._require_text(did, "did")
        try:
            didkey.public_key_from_did(did)
        except ValueError as exc:
            raise Exception(f"did is not a valid Ed25519 did:key: {exc}")
        existing = self._get(ctx, "citizen", owner_ref)
        if existing:
            if existing["did"] == did:
                return json.dumps(existing)
            raise Exception(f"Citizen {owner_ref[:12]}... is already registered with a different DID")
        now = self._now(ctx)
        citizen = {"docType": "citizen", "ownerRef": owner_ref, "did": did, "status": "ACTIVE",
                   "registeredBy": c["name"], "registeredAt": now}
        self._put(ctx, "citizen", owner_ref, citizen)
        self._emit(ctx, "CitizenRegistered", {"ownerRef": owner_ref, "did": did})
        return json.dumps(citizen)

    def ReadCitizen(self, ctx: Context, owner_ref: str) -> str:
        return json.dumps(self._must_get(ctx, "citizen", owner_ref, "Citizen"))

    def _verify_consents(self, ctx: Context, consent_json: str, survey_no: str, seller_ref: str,
                         buyer_ref: str, consideration: float | int) -> dict[str, Any]:
        """Both parties must have signed the same transfer intent with their registered DIDs."""
        try:
            consent = json.loads(consent_json)
            intent = consent["intent"]
            seller_sig, buyer_sig = consent["seller"], consent["buyer"]
        except (ValueError, KeyError, TypeError):
            raise Exception("consent must be JSON {intent, seller: {did, signature}, buyer: {did, signature}}")
        if intent.get("type") != INTENT_TYPE:
            raise Exception(f"consent intent must be of type {INTENT_TYPE}")
        expected = {"surveyNo": survey_no, "sellerRef": seller_ref, "buyerRef": buyer_ref}
        for field, value in expected.items():
            if intent.get(field) != value:
                raise Exception(f"consent intent {field} does not match this application")
        if not isinstance(intent.get("considerationINR"), int) or intent["considerationINR"] != consideration:
            raise Exception("consent intent considerationINR does not match this application")
        intent_id = self._require_text(intent.get("intentId"), "intent.intentId")
        try:
            created = dt.datetime.fromisoformat(str(intent.get("createdAt")).replace("Z", "+00:00"))
        except ValueError:
            raise Exception("consent intent createdAt is not an ISO timestamp")
        tx_time = ctx.stub.get_tx_timestamp()
        if created > tx_time + dt.timedelta(minutes=5) or tx_time - created > INTENT_MAX_AGE:
            raise Exception("consent intent is expired or dated in the future")
        if self._get(ctx, "consentused", intent_id):
            raise Exception(f"consent intent {intent_id} was already used for another application")

        message = canonical(intent)
        dids = {}
        for party, ref, sig in (("seller", seller_ref, seller_sig), ("buyer", buyer_ref, buyer_sig)):
            citizen = self._get(ctx, "citizen", ref)
            if not citizen or citizen.get("status") != "ACTIVE":
                raise Exception(f"{party} has no registered citizen identity (DID); register with the portal first")
            if sig.get("did") != citizen["did"]:
                raise Exception(f"{party} signed with {sig.get('did', 'no DID')}, not their registered DID")
            try:
                raw_sig = base64.b64decode(sig.get("signature", ""), validate=True)
            except (binascii.Error, ValueError):
                raw_sig = b""
            if not didkey.verify(citizen["did"], message, raw_sig):
                raise Exception(f"ACCESS DENIED: the {party}'s signature on the transfer intent is invalid")
            dids[party] = citizen["did"]
        return {"intentId": intent_id, "intentSha256": hashlib.sha256(message).hexdigest(),
                "sellerDid": dids["seller"], "buyerDid": dids["buyer"]}

    # =====================================================================
    # Org1 -- Registration
    # =====================================================================

    def RegisterParcel(self, ctx: Context, survey_no: str, village: str, district: str, area_sqm: str,
                       owner_name: str, owner_ref: str, boundary_geojson: str = "") -> str:
        """Registrar enters an existing parcel into the digital registry.

        `boundary_geojson` (a GeoJSON Polygon in [longitude, latitude]) is the surveyed boundary. When given, the
        area it encloses must agree with `area_sqm` within the survey tolerance."""
        c = self._require(ctx, "RegisterParcel", REGISTRATION, ["registrar"])
        survey_no = self._require_text(survey_no, "surveyNo")
        if self._get(ctx, "parcel", survey_no):
            raise Exception(f"Parcel {survey_no} is already registered")
        area = self._require_number(area_sqm, "areaSqm", allow_zero=False)
        boundary, shape = None, {}
        if str(boundary_geojson or "").strip():
            try:
                boundary = geometry.parse_boundary(boundary_geojson)
            except ValueError as exc:
                raise Exception(f"boundary is invalid: {exc}")
            shape = geometry.describe(boundary)
            deviation = abs(shape["boundaryAreaSqm"] - area) / area * 100
            if deviation > SURVEY_TOLERANCE_PCT:
                raise Exception(f"boundary encloses {shape['boundaryAreaSqm']} sq.m but the recorded area is {area} sq.m "
                                f"({_js_round_2dp(deviation)}% apart; the tolerance is {SURVEY_TOLERANCE_PCT}%)")
        now = self._now(ctx)
        parcel = {
            "docType": "parcel",
            "surveyNo": survey_no,
            "village": self._require_text(village, "village"),
            "district": self._require_text(district, "district"),
            "areaSqm": area,
            "ownerName": self._require_text(owner_name, "ownerName"),
            "ownerRef": self._require_text(owner_ref, "ownerRef"),
            "status": "ACTIVE",
            "pendingApplication": None,
            "lastTransfer": None,
            "boundary": boundary,
            **shape,
            "registeredBy": c["name"],
            "registeredAt": now,
            "updatedAt": now,
        }
        self._put(ctx, "parcel", survey_no, parcel)
        self._emit(ctx, "ParcelRegistered", {"surveyNo": survey_no, "ownerName": parcel["ownerName"],
                                             "hasBoundary": boundary is not None})
        return json.dumps(parcel)

    def SubmitTransferApplication(self, ctx: Context, app_id: str, survey_no: str, seller_ref: str,
                                  buyer_name: str, buyer_ref: str, consideration_inr: str,
                                  consent_json: str) -> str:
        """Clerk opens a transfer file. Locks the parcel against a second sale.

        New: `consent_json` must carry the seller's and buyer's DID signatures over
        the same transfer intent; the contract verifies both.
        """
        c = self._require(ctx, "SubmitTransferApplication", REGISTRATION, ["clerk"])
        app_id = self._require_text(app_id, "appId")
        seller_ref = self._require_text(seller_ref, "sellerRef")
        buyer_ref = self._require_text(buyer_ref, "buyerRef")

        if self._get(ctx, "application", app_id):
            raise Exception(f"Application {app_id} already exists")
        parcel = self._must_get(ctx, "parcel", survey_no, "Parcel")

        if parcel["status"] != "ACTIVE":
            raise Exception(
                f"Parcel {survey_no} is locked by pending application {parcel['pendingApplication']} "
                "-- a second transfer cannot be opened until that one is decided")
        if parcel["ownerRef"] != seller_ref:
            raise Exception(f"Seller is not the registered owner of parcel {survey_no}")
        if buyer_ref == seller_ref:
            raise Exception("Buyer and seller cannot be the same person")

        consideration = self._require_number(consideration_inr, "considerationINR")
        consent = self._verify_consents(ctx, consent_json, survey_no, seller_ref, buyer_ref, consideration)

        now = self._now(ctx)
        app = {
            "docType": "application",
            "appId": app_id,
            "kind": "TRANSFER",
            "surveyNo": survey_no,
            "sellerName": parcel["ownerName"],
            "sellerRef": seller_ref,
            "buyerName": self._require_text(buyer_name, "buyerName"),
            "buyerRef": buyer_ref,
            "considerationINR": consideration,
            "status": STATUS.SUBMITTED,
            "consent": {**consent, "verifiedAt": now},
            "documents": [],
            "verification": None,
            "survey": None,
            "decision": None,
            "createdBy": c["name"],
            "createdAt": now,
            "updatedAt": now,
            "trail": [],
        }
        self._trail(ctx, app, c, "SUBMITTED (seller and buyer DID signatures verified)")

        parcel["status"] = "TRANSFER_PENDING"
        parcel["pendingApplication"] = app_id
        parcel["updatedAt"] = now

        self._put(ctx, "consentused", consent["intentId"], {"docType": "consentused", "appId": app_id, "at": now})
        # Write-once facts about the file. The validator checks these instead of the
        # application record, so its transactions never contend with the officers'.
        self._put(ctx, "appmeta", app_id, {"docType": "appmeta", "appId": app_id, "surveyNo": survey_no,
                                            "sellerRef": seller_ref, "buyerRef": buyer_ref, "createdAt": now})
        self._put(ctx, "application", app_id, app)
        self._put(ctx, "parcel", survey_no, parcel)
        self._emit(ctx, "ApplicationSubmitted", {"appId": app_id, "surveyNo": survey_no})
        return json.dumps(app)

    def AnchorDocument(self, ctx: Context, app_id: str, doc_sha256: str, doc_id: str, document_type: str,
                       issuer: str) -> str:
        """Clerk anchors the SHA-256 fingerprint of a DigiLocker document to the file.

        A fingerprint can be anchored only once, so the same deed cannot be reused
        to back two different applications.
        """
        c = self._require(ctx, "AnchorDocument", REGISTRATION, ["clerk"])
        app = self._must_get(ctx, "application", app_id, "Application")
        self._must_status(app, [STATUS.SUBMITTED])

        sha = self._require_text(doc_sha256, "docSha256").lower()
        if not SHA256_RE.match(sha):
            raise Exception("docSha256 must be a 64-character hex SHA-256 digest")
        existing = self._get(ctx, "docanchor", sha)
        if existing:
            raise Exception(f"Document {sha[:12]}... is already anchored to application {existing['appId']}")

        anchor = {
            "docType": "docanchor",
            "sha256": sha,
            "appId": app_id,
            "docId": self._require_text(doc_id, "docId"),
            "documentType": self._require_text(document_type, "documentType"),
            "issuer": self._require_text(issuer, "issuer"),
            "anchoredBy": c["name"],
            "anchoredAt": self._now(ctx),
            "txId": ctx.stub.get_tx_id(),
        }
        app["documents"].append({
            "sha256": sha, "docId": anchor["docId"], "documentType": anchor["documentType"],
            "issuer": anchor["issuer"],
        })
        self._trail(ctx, app, c, f"ANCHORED {anchor['documentType']} {anchor['docId']}")

        self._put(ctx, "docanchor", sha, anchor)
        self._put(ctx, "application", app_id, app)
        self._emit(ctx, "DocumentAnchored", {"appId": app_id, "sha256": sha})
        return json.dumps(anchor)

    def ForwardToRevenue(self, ctx: Context, app_id: str) -> str:
        """Registrar hands the file to Revenue. This is the Org1 -> Org2 handoff
        (and the event the Agentic AI Validator listens for)."""
        c = self._require(ctx, "ForwardToRevenue", REGISTRATION, ["registrar"])
        app = self._must_get(ctx, "application", app_id, "Application")
        self._must_status(app, [STATUS.SUBMITTED])
        if len(app["documents"]) == 0:
            raise Exception(f"Application {app_id} has no anchored documents; anchor at least one before forwarding")
        app["status"] = STATUS.WITH_REVENUE
        self._trail(ctx, app, c, "FORWARDED to Revenue")
        self._put(ctx, "application", app_id, app)
        self._emit(ctx, "HandoffToRevenue",
                   {"appId": app_id, "surveyNo": app["surveyNo"], "documents": len(app["documents"])})
        return json.dumps(app)

    # =====================================================================
    # Org2 -- Revenue
    # =====================================================================

    def VerifyDocuments(self, ctx: Context, app_id: str, recomputed_hashes_json: str) -> str:
        """Verifier independently re-fetches every document from DigiLocker, hashes it,
        and submits the fingerprints. The contract compares them with the anchors
        written by Org1. Any difference is recorded on the ledger as FLAGGED."""
        c = self._require(ctx, "VerifyDocuments", REVENUE, ["verifier"])
        app = self._must_get(ctx, "application", app_id, "Application")
        self._must_status(app, [STATUS.WITH_REVENUE])

        try:
            submitted = json.loads(recomputed_hashes_json)
        except ValueError:
            raise Exception("recomputedHashesJson must be a JSON array of SHA-256 hex strings")
        if not isinstance(submitted, list):
            raise Exception("recomputedHashesJson must be a JSON array of SHA-256 hex strings")
        got = {str(h).lower() for h in submitted}
        anchored = {d["sha256"] for d in app["documents"]}

        missing = [d["docId"] for d in app["documents"] if d["sha256"] not in got]
        unexpected = [f"{h[:12]}..." for h in sorted(got) if h not in anchored]
        ok = len(missing) == 0 and len(unexpected) == 0

        app["verification"] = {
            "verifiedBy": c["name"],
            "at": self._now(ctx),
            "result": "MATCH" if ok else "MISMATCH",
            "documentsChecked": len(app["documents"]),
            "mismatchedDocuments": missing,
            "unexpectedFingerprints": unexpected,
        }
        app["status"] = STATUS.DOCS_VERIFIED if ok else STATUS.FLAGGED
        self._trail(ctx, app, c, "DOCUMENTS VERIFIED" if ok else "FLAGGED: fingerprint mismatch")
        self._put(ctx, "application", app_id, app)
        self._emit(ctx, "DocumentsVerified" if ok else "ApplicationFlagged",
                   {"appId": app_id, "result": app["verification"]["result"]})
        return json.dumps(app)

    def RecordSurvey(self, ctx: Context, app_id: str, measured_area_sqm: str, boundaries: str) -> str:
        """Surveyor records the field measurement; deviation beyond tolerance is flagged for the Collector."""
        c = self._require(ctx, "RecordSurvey", REVENUE, ["surveyor"])
        app = self._must_get(ctx, "application", app_id, "Application")
        self._must_status(app, [STATUS.DOCS_VERIFIED])
        parcel = self._must_get(ctx, "parcel", app["surveyNo"], "Parcel")

        measured = self._require_number(measured_area_sqm, "measuredAreaSqm", allow_zero=False)
        deviation = _num(_js_round_2dp(abs(measured - parcel["areaSqm"]) / parcel["areaSqm"] * 100))

        app["survey"] = {
            "surveyedBy": c["name"],
            "at": self._now(ctx),
            "recordedAreaSqm": parcel["areaSqm"],
            "measuredAreaSqm": measured,
            "deviationPct": deviation,
            "tolerancePct": SURVEY_TOLERANCE_PCT,
            "withinTolerance": deviation <= SURVEY_TOLERANCE_PCT,
            "boundaries": self._require_text(boundaries, "boundaries"),
        }
        app["status"] = STATUS.SURVEYED
        self._trail(ctx, app, c, f"SURVEYED (deviation {deviation}%)")
        self._put(ctx, "application", app_id, app)
        self._emit(ctx, "SurveyRecorded", {"appId": app_id, "deviationPct": deviation})
        return json.dumps(app)

    def ForwardToCollectorate(self, ctx: Context, app_id: str) -> str:
        """Verifier hands the completed file to the Collectorate. This is the Org2 -> Org3 handoff."""
        c = self._require(ctx, "ForwardToCollectorate", REVENUE, ["verifier"])
        app = self._must_get(ctx, "application", app_id, "Application")
        self._must_status(app, [STATUS.SURVEYED])
        app["status"] = STATUS.WITH_COLLECTORATE
        self._trail(ctx, app, c, "FORWARDED to Collectorate")
        self._put(ctx, "application", app_id, app)
        self._emit(ctx, "HandoffToCollectorate", {"appId": app_id})
        return json.dumps(app)

    # =====================================================================
    # Org2 -- the Agentic AI Validator: an identity, not an authority
    # =====================================================================

    def RegisterAgentKey(self, ctx: Context, agent_name: str, pgp_fingerprint: str, did: str,
                         public_key_armored: str) -> str:
        """A human Revenue verifier attests which PGP key belongs to the validator agent.

        The agent cannot register or rotate its own key. Old keys are kept (marked
        ROTATED) so reports signed before a rotation still verify."""
        c = self._require(ctx, "RegisterAgentKey", REVENUE, ["verifier"])
        agent_name = self._require_text(agent_name, "agentName")
        fpr = self._require_text(pgp_fingerprint, "pgpFingerprint").upper().replace(" ", "")
        if not FINGERPRINT_RE.match(fpr):
            raise Exception("pgpFingerprint must be a 40-hex-digit OpenPGP v4 fingerprint")
        did = self._require_text(did, "did")
        try:
            didkey.public_key_from_did(did)
        except ValueError as exc:
            raise Exception(f"did is not a valid Ed25519 did:key: {exc}")
        armored = self._require_text(public_key_armored, "publicKeyArmored")
        if "BEGIN PGP PUBLIC KEY BLOCK" not in armored:
            raise Exception("publicKeyArmored must be an ASCII-armored OpenPGP public key")

        now = self._now(ctx)
        existing = self._get(ctx, "agentkey", agent_name)
        previous: list[dict[str, Any]] = []
        if existing:
            if existing["pgpFingerprint"] == fpr:
                return json.dumps(existing)
            previous = existing.get("previousKeys", []) + [{
                "pgpFingerprint": existing["pgpFingerprint"], "did": existing["did"],
                "attestedBy": existing["attestedBy"], "attestedAt": existing["attestedAt"], "rotatedAt": now}]
        record = {
            "docType": "agentkey", "agentName": agent_name, "pgpFingerprint": fpr, "did": did,
            "publicKey": armored, "status": "ACTIVE", "attestedBy": c["name"], "attestedAt": now,
            "previousKeys": previous,
        }
        self._put(ctx, "agentkey", agent_name, record)
        self._emit(ctx, "AgentKeyRegistered", {"agentName": agent_name, "pgpFingerprint": fpr})
        return json.dumps(record)

    def SubmitValidationReport(self, ctx: Context, app_id: str, report_sha256: str, report_cid: str,
                               trace_cid: str, verdict: str, flag_count: str, confidence: str) -> str:
        """The validator anchors its PGP-signed report. Evidence only: nothing about the
        application changes and nothing is approved or rejected.

        Design note (MVCC): every officer action rewrites the application record. If
        this function read and rewrote it too, the agent's transaction would lose the
        race whenever an officer acted in the same block (MVCC_READ_CONFLICT). So the
        report lives under its own keys and only the write-once `appmeta` is read;
        ReadApplication joins the two at query time."""
        c = self._require(ctx, "SubmitValidationReport", REVENUE, ["agent"])
        key = self._get(ctx, "agentkey", c["name"])
        if not key or key.get("status") != "ACTIVE":
            raise Exception(f"agent {c['name']} has no PGP key attested by a Revenue officer")
        self._must_get(ctx, "appmeta", app_id, "Application")

        sha = self._require_text(report_sha256, "reportSha256").lower()
        if not SHA256_RE.match(sha):
            raise Exception("reportSha256 must be a 64-character hex SHA-256 digest")
        if self._get(ctx, "valreport", sha):
            raise Exception(f"Report {sha[:12]}... is already anchored")
        for cid, field in ((report_cid, "reportCid"), (trace_cid, "traceCid")):
            if not CID_RE.match(self._require_text(cid, field)):
                raise Exception(f"{field} must be a base32 CIDv1 (b...)")
        if verdict not in VERDICTS:
            raise Exception(f"verdict must be one of {', '.join(VERDICTS)}")
        flags = int(self._require_number(flag_count, "flagCount"))
        conf = self._require_number(confidence, "confidence")
        if conf > 1:
            raise Exception("confidence must be between 0 and 1")

        anchor = {
            "docType": "valreport", "sha256": sha, "appId": app_id, "reportCid": report_cid,
            "traceCid": trace_cid, "verdict": verdict, "flagCount": flags, "confidence": conf,
            "agent": c["name"], "agentDid": key["did"], "pgpFingerprint": key["pgpFingerprint"],
            "anchoredAt": self._now(ctx), "txId": ctx.stub.get_tx_id(),
        }
        self._put(ctx, "valreport", sha, anchor)
        ctx.stub.put_state(ctx.stub.create_composite_key("appvalidation", [app_id, sha]),
                           json.dumps(anchor).encode("utf-8"))
        self._emit(ctx, "ValidationReportAnchored", {"appId": app_id, "sha256": sha, "verdict": verdict})
        return json.dumps(anchor)

    def _validations_by_app(self, ctx: Context, app_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
        attrs = [app_id] if app_id else []
        out: dict[str, list[dict[str, Any]]] = {}
        for _k, v in ctx.stub.get_state_by_partial_composite_key("appvalidation", attrs):
            a = json.loads(v.decode("utf-8"))
            out.setdefault(a["appId"], []).append(a)
        for lst in out.values():
            lst.sort(key=lambda a: a["anchoredAt"])
        return out

    def _with_evidence(self, app: dict[str, Any], validations: list[dict[str, Any]]) -> dict[str, Any]:
        """Query-time view: the application plus the validator's anchored reports, also
        shown in the trail so 'who did what' distinguishes evidence from decisions."""
        view = dict(app)
        view["validations"] = validations
        agent_lines = [{"at": v["anchoredAt"], "by": v["agent"], "org": REVENUE, "role": "agent",
                        "action": f"AGENT REPORT anchored: {v['verdict']} ({v['flagCount']} flag"
                                  f"{'s' if v['flagCount'] != 1 else ''}) -- evidence only",
                        "status": None, "txId": v["txId"]} for v in validations]
        view["trail"] = sorted(app["trail"] + agent_lines, key=lambda t: t["at"])
        return view

    def VerifyValidationReport(self, ctx: Context, report_sha256: str) -> str:
        """Public check: was a report with this exact SHA-256 anchored, by which key?"""
        sha = str(report_sha256 or "").lower()
        anchor = self._get(ctx, "valreport", sha)
        return json.dumps({"anchored": True, **anchor} if anchor else {"anchored": False, "sha256": sha})

    def ReadAgentKey(self, ctx: Context, agent_name: str) -> str:
        return json.dumps(self._must_get(ctx, "agentkey", agent_name, "Agent key"))

    def ListAgentKeys(self, ctx: Context) -> str:
        return json.dumps(self._scan(ctx, "agentkey"))

    # =====================================================================
    # Org3 -- the Records Office key that signs downloadable transfer records
    # =====================================================================

    def RegisterRecordsKey(self, ctx: Context, pgp_fingerprint: str, public_key_armored: str) -> str:
        """The Collector attests the OpenPGP key the Records Office signs transfer records with.

        Anyone holding a downloaded record can then check it was signed by the key the ledger names."""
        c = self._require(ctx, "RegisterRecordsKey", COLLECTORATE, ["collector"])
        fpr = self._require_text(pgp_fingerprint, "pgpFingerprint").upper().replace(" ", "")
        if not FINGERPRINT_RE.match(fpr):
            raise Exception("pgpFingerprint must be a 40-hex-digit OpenPGP v4 fingerprint")
        armored = self._require_text(public_key_armored, "publicKeyArmored")
        if "BEGIN PGP PUBLIC KEY BLOCK" not in armored:
            raise Exception("publicKeyArmored must be an ASCII-armored OpenPGP public key")
        now = self._now(ctx)
        existing = self._get(ctx, "recordskey", "records-office")
        previous: list[dict[str, Any]] = []
        if existing:
            if existing["pgpFingerprint"] == fpr:
                return json.dumps(existing)
            previous = existing.get("previousKeys", []) + [{
                "pgpFingerprint": existing["pgpFingerprint"], "attestedBy": existing["attestedBy"],
                "attestedAt": existing["attestedAt"], "rotatedAt": now}]
        record = {"docType": "recordskey", "name": "records-office", "pgpFingerprint": fpr, "publicKey": armored,
                  "status": "ACTIVE", "attestedBy": c["name"], "attestedAt": now, "previousKeys": previous}
        self._put(ctx, "recordskey", "records-office", record)
        self._emit(ctx, "RecordsKeyRegistered", {"pgpFingerprint": fpr})
        return json.dumps(record)

    def ReadRecordsKey(self, ctx: Context) -> str:
        return json.dumps(self._must_get(ctx, "recordskey", "records-office", "Records key"))

    # =====================================================================
    # Org3 -- Collectorate (the only place ownership can change)
    # =====================================================================

    def ApproveTransfer(self, ctx: Context, app_id: str, remarks: str) -> str:
        c = self._require(ctx, "ApproveTransfer", COLLECTORATE, ["collector"])
        app = self._must_get(ctx, "application", app_id, "Application")
        self._must_status(app, [STATUS.WITH_COLLECTORATE])
        parcel = self._must_get(ctx, "parcel", app["surveyNo"], "Parcel")

        if parcel["pendingApplication"] != app_id or parcel["ownerRef"] != app["sellerRef"]:
            raise Exception(f"Parcel {app['surveyNo']} no longer matches application {app_id}; refusing to transfer")

        now = self._now(ctx)
        app["status"] = STATUS.APPROVED
        app["decision"] = {"by": c["name"], "at": now, "outcome": "APPROVED", "remarks": remarks or ""}
        self._trail(ctx, app, c, "APPROVED -- ownership transferred")

        parcel["lastTransfer"] = {
            "appId": app_id, "fromName": parcel["ownerName"], "toName": app["buyerName"], "at": now,
            "txId": ctx.stub.get_tx_id(),
        }
        parcel["ownerName"] = app["buyerName"]
        parcel["ownerRef"] = app["buyerRef"]
        parcel["status"] = "ACTIVE"
        parcel["pendingApplication"] = None
        parcel["updatedAt"] = now

        self._put(ctx, "application", app_id, app)
        self._put(ctx, "parcel", app["surveyNo"], parcel)
        self._emit(ctx, "TransferApproved", {"appId": app_id, "surveyNo": app["surveyNo"], "newOwner": app["buyerName"]})
        return json.dumps(app)

    def RejectApplication(self, ctx: Context, app_id: str, reason: str) -> str:
        c = self._require(ctx, "RejectApplication", COLLECTORATE, ["collector"])
        app = self._must_get(ctx, "application", app_id, "Application")
        if app["status"] in (STATUS.APPROVED, STATUS.REJECTED):
            raise Exception(f"Application {app_id} is already {app['status']}")
        now = self._now(ctx)
        app["status"] = STATUS.REJECTED
        app["decision"] = {"by": c["name"], "at": now, "outcome": "REJECTED",
                           "remarks": self._require_text(reason, "reason")}
        self._trail(ctx, app, c, "REJECTED")

        parcel = self._must_get(ctx, "parcel", app["surveyNo"], "Parcel")
        if parcel["pendingApplication"] == app_id:
            parcel["status"] = "ACTIVE"
            parcel["pendingApplication"] = None
            parcel["updatedAt"] = now
            self._put(ctx, "parcel", app["surveyNo"], parcel)
        self._put(ctx, "application", app_id, app)
        self._emit(ctx, "ApplicationRejected", {"appId": app_id})
        return json.dumps(app)

    # =====================================================================
    # Queries -- open to every channel member (transparency)
    # =====================================================================

    def ReadParcel(self, ctx: Context, survey_no: str) -> str:
        return json.dumps(self._must_get(ctx, "parcel", survey_no, "Parcel"))

    def ReadApplication(self, ctx: Context, app_id: str) -> str:
        app = self._must_get(ctx, "application", app_id, "Application")
        return json.dumps(self._with_evidence(app, self._validations_by_app(ctx, app_id).get(app_id, [])))

    def VerifyDocument(self, ctx: Context, doc_sha256: str) -> str:
        """Public check: has this exact document been anchored, and to what?"""
        sha = str(doc_sha256 or "").lower()
        anchor = self._get(ctx, "docanchor", sha)
        return json.dumps({"anchored": True, **anchor} if anchor else {"anchored": False, "sha256": sha})

    def OwnershipChain(self, ctx: Context, survey_no: str) -> str:
        """The chain of owners of a parcel, oldest first, derived from every committed version of it.

        Each link says who held the land, from which transaction until which, and how they acquired it
        (first registration or an approved transfer, with its application and consideration)."""
        self._must_get(ctx, "parcel", survey_no, "Parcel")
        links: list[dict[str, Any]] = []
        for h in self._history(ctx, "parcel", survey_no):
            v = h.get("value")
            if not v or (links and links[-1]["ownerRef"] == v["ownerRef"]):
                continue
            if links:
                links[-1].update(until=h["timestamp"], untilTxId=h["txId"])
            t = v.get("lastTransfer") if links else None
            link: dict[str, Any] = {"seq": len(links) + 1, "ownerName": v["ownerName"], "ownerRef": v["ownerRef"],
                                    "since": (t or {}).get("at") or v.get("registeredAt") or h["timestamp"],
                                    "sinceTxId": h["txId"], "until": None, "untilTxId": None,
                                    "acquired": "transfer" if t else "registration"}
            if t:
                app = self._get(ctx, "application", t["appId"]) or {}
                link.update(appId=t["appId"], fromName=t.get("fromName"), considerationINR=app.get("considerationINR"),
                            decidedBy=(app.get("decision") or {}).get("by"))
            else:
                link.update(registeredBy=v.get("registeredBy"))
            links.append(link)
        return json.dumps({"surveyNo": survey_no, "owners": len(links), "links": links})

    def ParcelHistory(self, ctx: Context, survey_no: str) -> str:
        """Every committed version of a parcel: the chain of ownership."""
        self._must_get(ctx, "parcel", survey_no, "Parcel")
        return json.dumps(self._history(ctx, "parcel", survey_no))

    def ApplicationHistory(self, ctx: Context, app_id: str) -> str:
        self._must_get(ctx, "application", app_id, "Application")
        return json.dumps(self._history(ctx, "application", app_id))

    def ListApplications(self, ctx: Context, status: str) -> str:
        """status = '' or 'ALL' returns everything."""
        all_apps = self._scan(ctx, "application")
        want = str(status or "").upper()
        chosen = [a for a in all_apps if a["status"] == want] if want and want != "ALL" else all_apps
        evidence = self._validations_by_app(ctx)
        return json.dumps([self._with_evidence(a, evidence.get(a["appId"], [])) for a in chosen])

    def ListParcels(self, ctx: Context) -> str:
        return json.dumps(self._scan(ctx, "parcel"))
