"""
Downloadable transfer records, signed with OpenPGP.

Every transfer file has two kinds of record, each one text file that carries
its own signature (an OpenPGP cleartext-signed message, so the text stays
readable and `gpg --verify` checks it):

  public copy   anyone may download it. Parcel, boundary, every step with its
                ledger transaction and block, document fingerprints, the
                validator's report fingerprint and verdict, and the parcel's
                chain of ownership. People appear as initials; no amounts, no
                identifiers that lead to a person.

  party copy    only the seller or the buyer of that transfer may download it,
                and each gets their own ("issued to"). Adds full names, both
                parties' DIDs and consent signatures, the consideration, the
                DigiLocker document IDs, the officers' names on each step, the
                survey measurement, the validator's explanation for citizens,
                and the recipient's own DigiLocker access log for this file.

Both are signed by the Records Office key, which the Collector attests on the
ledger (RegisterRecordsKey), so a record can be checked against the key the
ledger names, without trusting this website. Each record ends with the same
content as canonical JSON, for machines.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import textwrap
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.pgp import SigningKey, format_fingerprint, verify_cleartext
from chaincode.landrec.landrec import canonical
from nodes.common import config

RECORD_TYPE = "landchain.transfer-record/v1"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")
WIDTH = 78
STATUS_TEXT = {
    "SUBMITTED": "Filed at Registration", "WITH_REVENUE": "With Revenue", "DOCS_VERIFIED": "Documents verified",
    "FLAGGED": "Flagged by Revenue", "SURVEYED": "Surveyed", "WITH_COLLECTORATE": "With the Collector",
    "APPROVED": "Approved: ownership transferred", "REJECTED": "Rejected",
}


# ---------------------------------------------------------------------------
# The Records Office key
# ---------------------------------------------------------------------------

@dataclass
class RecordsKey:
    pgp: SigningKey
    public_armored: str

    @property
    def fingerprint(self) -> str:
        return self.pgp.fingerprint


def load_or_create_key() -> RecordsKey:
    d = config.data_dir("gateway") / "keys"
    d.mkdir(parents=True, exist_ok=True)
    key_file, meta_file = d / "records.key", d / "records.json"
    if key_file.exists() and meta_file.exists():
        private = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
        assert isinstance(private, Ed25519PrivateKey)
        meta = json.loads(meta_file.read_text())
        pgp = SigningKey(private, meta["userId"], meta["created"])
    else:
        pgp = SigningKey.generate("LandChain Records Office (Hanamkonda district) <records@landchain.example>")
        key_file.write_bytes(pgp.private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
        os.chmod(key_file, 0o600)
        meta_file.write_text(json.dumps({"userId": pgp.user_id, "created": pgp.created}))
    armored = pgp.public_key_armored(comment="LandChain Records Office")
    (d / "records.pub.asc").write_text(armored)
    return RecordsKey(pgp, armored)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def initials(name: str | None) -> str:
    return " ".join(f"{p[0].upper()}." for p in str(name or "").split() if p[:1].isalpha()) or "(unknown)"


def when(ts: str | None) -> str:
    if not ts:
        return "-"
    try:
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(IST)
    except ValueError:
        return ts
    return t.strftime("%d %b %Y, %H:%M IST")


def inr(amount: Any) -> str:
    try:
        n = int(amount)
    except (TypeError, ValueError):
        return "-"
    s = str(n)
    if len(s) <= 3:
        return f"Rs. {s}"
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return "Rs. " + ",".join(([head] if head else []) + groups + [tail])


def short_did(did: str | None) -> str:
    return f"{did[:16]}...{did[-6:]}" if did else "-"


def heading(title: str) -> list[str]:
    return ["", title.upper(), "~" * len(title)]


def kv(rows: list[tuple[str, Any]]) -> list[str]:
    width = max(len(k) for k, _ in rows) + 2
    out = []
    for k, v in rows:
        text = str(v if v not in (None, "") else "-")
        wrapped = textwrap.wrap(text, WIDTH - width) or ["-"]
        out.append(f"{(k + ':').ljust(width)}{wrapped[0]}")
        out += [" " * width + w for w in wrapped[1:]]
    return out


def para(text: str) -> list[str]:
    return textwrap.wrap(text, WIDTH)


# ---------------------------------------------------------------------------
# Building a record
# ---------------------------------------------------------------------------

def build(copy: str, ctx: dict[str, Any], key: RecordsKey, recipient: dict[str, Any] | None = None) -> dict[str, Any]:
    """copy = "public" or "party". ctx holds what the gateway read from the ledger, DigiLocker and the validator."""
    a, parcel, chain = ctx["application"], ctx["parcel"], ctx["chain"]
    blocks: dict[str, int | None] = ctx.get("blocks", {})
    party = copy == "party"
    role = (recipient or {}).get("role")
    issued_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    suffix = {"seller": "SELLER", "buyer": "BUYER"}.get(role or "", "PUBLIC")
    record_id = f"TR-{a['appId']}-{suffix}-{ctx['height']}"
    name = (lambda n: n or "-") if party else initials

    shape = {k: parcel.get(k) for k in ("boundaryAreaSqm", "perimeterM", "centroid", "corners")}
    scrub = (lambda text: text) if party else (lambda text: re.sub(r"\bDOC-[0-9a-f]+", "(document)", text))
    steps = [{"at": t["at"], "action": scrub(t["action"]), "status": t["status"], "org": t["org"], "role": t["role"],
              "by": t["by"] if party else None, "txId": t["txId"], "block": blocks.get(t["txId"])} for t in a["trail"]]
    docs = [{"type": d["documentType"], "issuer": d["issuer"], "sha256": d["sha256"],
             "docId": d["docId"] if party else None} for d in a["documents"]]
    validations = [{"verdict": v["verdict"], "flagCount": v.get("flagCount"), "reportSha256": v.get("sha256"),
                    "reportCid": v.get("reportCid"), "agent": v.get("agent"), "pgpFingerprint": v.get("pgpFingerprint"),
                    "at": v.get("anchoredAt"), "txId": v.get("txId"), "block": blocks.get(v.get("txId"))}
                   for v in a.get("validations", [])]
    owners = [{"seq": l["seq"], "owner": name(l["ownerName"]), "since": l["since"], "until": l["until"],
               "acquired": l["acquired"], "appId": l.get("appId"), "sinceTxId": l["sinceTxId"],
               "block": blocks.get(l["sinceTxId"]),
               **({"considerationINR": l.get("considerationINR")} if party and l.get("appId") == a["appId"] else {})}
              for l in chain["links"]]
    data: dict[str, Any] = {
        "recordType": RECORD_TYPE, "copy": copy, "recordId": record_id, "issuedAt": issued_at,
        "issuer": {"name": "LandChain Records Office", "pgpFingerprint": key.fingerprint,
                   "attestedOnLedger": ctx.get("keyOnLedger")},
        "ledger": {"channel": config.channel_name(), "heightAtIssue": ctx["height"]},
        "parcel": {"surveyNo": parcel["surveyNo"], "village": parcel["village"], "district": parcel["district"],
                   "recordedAreaSqm": parcel["areaSqm"], **shape, "boundary": parcel.get("boundary"),
                   "currentOwner": name(parcel["ownerName"]), "status": parcel["status"]},
        "transfer": {"applicationId": a["appId"], "status": a["status"], "statusText": STATUS_TEXT.get(a["status"], a["status"]),
                     "openedAt": a.get("submittedAt") or (a["trail"][0]["at"] if a["trail"] else None),
                     "seller": name(a["sellerName"]), "buyer": name(a["buyerName"]),
                     "decision": ({k: (a["decision"].get(k) if (party or k != "by") else None) for k in ("outcome", "at", "by", "remarks")}
                                  if a.get("decision") else None),
                     "consent": {"intentSha256": (a.get("consent") or {}).get("intentSha256"),
                                 "sellerDid": (a.get("consent") or {}).get("sellerDid") if party else short_did((a.get("consent") or {}).get("sellerDid")),
                                 "buyerDid": (a.get("consent") or {}).get("buyerDid") if party else short_did((a.get("consent") or {}).get("buyerDid"))}},
        "steps": steps, "documents": docs, "validations": validations, "ownershipChain": owners,
    }
    if party:
        consent = ctx.get("intent") or {}
        data["issuedTo"] = {"name": recipient["name"], "role": role, "digilockerId": recipient["digilockerId"],
                            "did": recipient["did"]}
        data["transfer"].update(considerationINR=a.get("considerationINR"),
                                intent=consent.get("intent"),
                                consentSignatures={"seller": consent.get("sellerSignature"),
                                                   "buyer": consent.get("buyerSignature")} if consent else None)
        data["survey"] = a.get("survey")
        data["verification"] = a.get("verification")
        data["validatorExplanation"] = ctx.get("citizenExplanation")
        data["accessLog"] = ctx.get("accessLog", [])

    text = render(data, party)
    signed = key.pgp.sign_cleartext(text)
    who = f"{role}-copy" if party else "public"
    return {"recordId": record_id, "filename": f"landchain-{a['appId']}-{who}.asc", "text": signed, "data": data}


def render(d: dict[str, Any], party: bool) -> str:
    p, t = d["parcel"], d["transfer"]
    title = "LANDCHAIN TRANSFER RECORD -- " + ("PARTY COPY" if party else "PUBLIC COPY")
    lines = [title, "=" * len(title)]
    lines += kv([("Record ID", d["recordId"]), ("Issued", when(d["issuedAt"])),
                 ("Issued by", f"{d['issuer']['name']}, key {format_fingerprint(d['issuer']['pgpFingerprint'])}"),
                 ("Key on ledger", "attested by " + d["issuer"]["attestedOnLedger"]["attestedBy"]
                  if d["issuer"].get("attestedOnLedger") else "not attested yet"),
                 ("Ledger", f"channel {d['ledger']['channel']}, height {d['ledger']['heightAtIssue']} when issued")])
    if party:
        r = d["issuedTo"]
        lines += kv([("Issued to", f"{r['name']} ({r['role']}), DigiLocker {r['digilockerId']}")])
        lines += [""] + para("This copy is for the person named above. It contains personal details; share it only "
                             "with people you trust. Anyone can download the public copy of the same transfer.")
    else:
        lines += [""] + para("Public copy. People are shown as initials and amounts are left out; the parties to the "
                             "transfer can download their own copy with full details.")

    lines += heading("Parcel")
    rows = [("Survey number", p["surveyNo"]), ("Village", f"{p['village']}, {p['district']} district, Telangana"),
            ("Recorded area", f"{p['recordedAreaSqm']} sq.m")]
    if p.get("boundaryAreaSqm"):
        rows += [("Surveyed boundary", f"{p['boundaryAreaSqm']} sq.m inside {p['corners']} corners, perimeter {p['perimeterM']} m"),
                 ("Centroid", f"{p['centroid'][1]:.6f} N, {p['centroid'][0]:.6f} E (WGS 84)")]
    rows += [("Current owner", p["currentOwner"]), ("Parcel status", p["status"])]
    lines += kv(rows)
    if p.get("boundary"):
        corners = p["boundary"]["coordinates"][0][:-1]
        lines += ["Boundary corners (longitude, latitude):"]
        lines += [f"  {i + 1:>2}. {c[0]:.7f}, {c[1]:.7f}" for i, c in enumerate(corners)]

    lines += heading("Transfer")
    rows = [("Application", t["applicationId"]), ("Status", t["statusText"]), ("Opened", when(t["openedAt"])),
            ("Seller", t["seller"]), ("Buyer", t["buyer"])]
    if party:
        rows.append(("Consideration", inr(t.get("considerationINR"))))
    rows += [("Consent", "both parties signed the same transfer intent with their registered keys"),
             ("Intent fingerprint", t["consent"]["intentSha256"]),
             ("Seller's key", t["consent"]["sellerDid"]), ("Buyer's key", t["consent"]["buyerDid"])]
    if t.get("decision"):
        dec = t["decision"]
        rows.append(("Decision", f"{dec['outcome']} on {when(dec['at'])}" + (f" by {dec['by']}" if dec.get("by") else "")
                     + (f": {dec['remarks']}" if dec.get("remarks") else "")))
    lines += kv(rows)
    if party and t.get("consentSignatures"):
        lines += ["", "Consent signatures (Ed25519 over the canonical intent, base64):"]
        for who in ("seller", "buyer"):
            s = (t["consentSignatures"] or {}).get(who) or {}
            if s.get("signature"):
                lines += [f"  {who}: {s['signature']}"]

    lines += heading("Every step, on the ledger")
    for s in d["steps"]:
        who = f"{s['by']} ({s['role']}, {s['org']})" if s.get("by") else f"{s['role']} of {s['org']}"
        lines += textwrap.wrap(f"{when(s['at'])}  {s['action']} -- {who}", WIDTH, subsequent_indent="    ")
        lines += [f"    tx {s['txId']}" + (f", block {s['block']}" if s.get("block") is not None else "")]

    lines += heading("Documents (held in DigiLocker; only fingerprints are on the ledger)")
    for doc in d["documents"]:
        lines += [f"{doc['type']} from {doc['issuer']}" + (f", {doc['docId']}" if doc.get("docId") else ""),
                  f"    SHA-256 {doc['sha256']}"]
    if party and d.get("survey"):
        sv = d["survey"]
        lines += heading("Field survey")
        lines += kv([("Measured area", f"{sv.get('measuredAreaSqm')} sq.m ({sv.get('deviationPct')}% from the record)"),
                     ("Boundaries", sv.get("boundaries")), ("Surveyed by", sv.get("by")), ("On", when(sv.get("at")))])

    lines += heading("AI validator's reports (evidence only; it decides nothing)")
    if not d["validations"]:
        lines += ["None yet."]
    for v in d["validations"]:
        lines += [f"{when(v['at']) if v.get('at') else 'Report'}  verdict {v['verdict']} ({v.get('flagCount', 0)} flag(s)), signed by key {v.get('pgpFingerprint') or '-'}",
                  f"    report SHA-256 {v['reportSha256']}" + (f", block {v['block']}" if v.get("block") is not None else "")]
    if party and d.get("validatorExplanation"):
        lines += ["", "What the validator told the parties:"] + ["  " + x for x in para(d["validatorExplanation"])]

    lines += heading("Chain of ownership")
    for o in d["ownershipChain"]:
        how = "first registration" if o["acquired"] == "registration" else f"transfer {o['appId']}"
        span = f"{when(o['since'])} to {when(o['until']) if o['until'] else 'today'}"
        lines += [f"{o['seq']}. {o['owner']} -- {how}", f"    {span}; tx {o['sinceTxId'][:16]}..."
                  + (f", block {o['block']}" if o.get("block") is not None else "")
                  + (f"; consideration {inr(o['considerationINR'])}" if o.get("considerationINR") else "")]

    if party and d.get("accessLog"):
        lines += heading("Who read your documents for this file")
        for e in d["accessLog"][:20]:
            lines += textwrap.wrap(f"{when(e.get('at'))}  {e.get('department') or e.get('requester')}: "
                                   f"{e.get('action', '')}" + (f" ({e['purpose']})" if e.get("purpose") else ""),
                                   WIDTH, subsequent_indent="    ")

    lines += heading("How to check this record")
    lines += para("1. The signature: import the Records Office key (shown at /verify and on the ledger) and run "
                  "gpg --verify on this file, or drop it on the LandChain /verify page.")
    lines += para("2. The ledger: each transaction ID above can be looked up on any department's peer; the document "
                  "and report fingerprints can be checked at /verify.")
    lines += heading("Machine-readable copy (canonical JSON)")
    lines += chunk(canonical(d).decode())
    lines += ["-- end of record --"]
    return "\n".join(line.rstrip() for line in lines)


def chunk(body: str, width: int = WIDTH) -> list[str]:
    """Fixed-width slices that rejoin to exactly `body`. No slice ends in a space, because a signed text line
    loses its trailing blanks."""
    out, i = [], 0
    while i < len(body):
        j = min(i + width, len(body))
        while j < len(body) and j > i + 1 and body[j - 1] == " ":
            j -= 1
        out.append(body[i:j])
        i = j
    return out


def parse_json_section(text: str) -> dict[str, Any] | None:
    """The canonical JSON at the end of a record (its lines rejoined)."""
    marker = "MACHINE-READABLE COPY (CANONICAL JSON)"
    if marker not in text:
        return None
    tail = text.split(marker, 1)[1].split("\n", 2)[2]
    body = tail.split("\n-- end of record --")[0]
    try:
        return json.loads(body.replace("\n", ""))
    except ValueError:
        return None


def verify(message: str, public_armored: str) -> dict[str, Any]:
    v, text = verify_cleartext(public_armored, message)
    data = parse_json_section(text) if text else None
    return {"valid": v.valid, "reason": v.reason, "fingerprint": v.fingerprint, "signedAt": v.created,
            "record": {k: data.get(k) for k in ("recordId", "copy", "issuedAt")} if data else None,
            "applicationId": ((data or {}).get("transfer") or {}).get("applicationId")}
