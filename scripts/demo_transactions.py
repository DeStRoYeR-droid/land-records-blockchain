#!/usr/bin/env python3
"""
demo_transactions.py -- the automated land transfer, end to end.

Every write below is signed by a real CA-issued certificate, endorsed by all
three departments' peers, ordered by Raft and committed.

    Act 1  Identities      what each X.509 certificate tells the chaincode
    Act 2  Citizens        DigiLocker accounts, did:key identities, documents
    Act 3  Registration    parcel, DID-signed transfer intent, anchoring, handoff
    Act 4  Validator       the agent investigates, signs, anchors -- evidence only
    Act 5  Revenue         independent re-verification and survey
    Act 6  Collectorate    the human decision; ownership moves
    Act 7  Attacks         what the network (and the validator) refuse or catch
    Act 8  Audit           public checks anyone on the channel can run, the chain of ownership,
                           and the PGP-signed transfer records anyone can download

    ./demo_transactions.sh               (network must be up: ./startup.sh --no-demo)
    DEMO_PAUSE=2 ./demo_transactions.sh  pause between acts when presenting
    ./demo_transactions.sh --seed 0      skip the extra approved transfers that give the
                                         valuation checks comparables (default: 3)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
from geo import layout  # noqa: E402
from nodes.sdk import ChaincodeError, Gateway  # noqa: E402

TTY = sys.stdout.isatty()
B, D, R, G, Y, BL, C, X = (("\033[1m", "\033[2m", "\033[31m", "\033[32m", "\033[33m", "\033[34m", "\033[36m", "\033[0m")
                           if TTY else ("",) * 8)
PAUSE = float(os.environ.get("DEMO_PAUSE", "0"))
DL = config.url("digilocker")
AGENT = config.url("agent")
GATEWAY = config.url("gateway")
SRO = "Sub-Registrar Office, Hanamkonda"
ORG = {"Org1MSP": "Org1 Registration", "Org2MSP": "Org2 Revenue", "Org3MSP": "Org3 Collectorate"}


def act(title: str) -> None:
    print(f"\n{B}{BL}---- {title} ----{X}")
    if PAUSE:
        time.sleep(PAUSE)


def note(text: str) -> None:
    print(f"  {D}{text}{X}")


def info(text: str) -> None:
    print(f"  {text}")


class Citizen:
    def __init__(self, name: str, mobile: str, last4: str):
        self.name = name
        resp = httpx.post(f"{DL}/users/signup", json={"name": name, "mobile": mobile, "aadhaar_last4": last4})
        resp.raise_for_status()
        self.dl_id = resp.json()["digilockerId"]
        self.ref = sha256_hex(f"landchain-owner:{self.dl_id}".encode())
        self.key = Ed25519PrivateKey.generate()  # in the web app this key lives in the citizen's browser
        self.did = didkey.did_from_public_key(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    def sign(self, intent: dict) -> dict:
        return {"did": self.did, "signature": base64.b64encode(self.key.sign(canonical(intent))).decode()}


def issue(holder: Citizen, doc_type: str, description: str, content: str) -> dict:
    resp = httpx.post(f"{DL}/issuer/documents", json={"digilocker_id": holder.dl_id, "doc_type": doc_type,
                                                      "org_name": SRO, "description": description, "content": content})
    resp.raise_for_status()
    return resp.json()


def dl_fetch(requester: str, holder: Citizen, doc_type: str, doc_id: str | None = None) -> dict:
    """DigiLocker consent flow: request -> citizen approves with OTP -> fetch."""
    req = httpx.post(f"{DL}/consent/request", json={"requester_name": requester,
                                                    "redirect_url": "https://landchain.example/callback"}).json()
    httpx.post(f"{DL}/consent/{req['id']}/verify-otp", json={"digilocker_id": holder.dl_id, "otp": "123456"}).raise_for_status()
    resp = httpx.post(f"{DL}/consent/{req['id']}/document",
                      json={"doc_type": doc_type, "org_name": SRO, "consent": "Y", "doc_id": doc_id})
    resp.raise_for_status()
    return resp.json()


def deed_text(survey: str, village: str, extent: int, vendor: str, purchaser: str, amount: str) -> str:
    return (f"SALE DEED\nSurvey No: {survey}, {village}, Hanamkonda, Telangana\nExtent: {extent} sq.m\n"
            f"Vendor: {vendor}\nPurchaser: {purchaser}\nConsideration: Rs. {amount}\nRegistered at {SRO}")


def ec_text(survey: str, applicant: str = "") -> str:
    return (f"ENCUMBRANCE CERTIFICATE\nSurvey No: {survey}\nPeriod searched: 2011-2026\nResult: NIL encumbrance"
            + (f"\nIssued to: {applicant}" if applicant else ""))


def intent(survey: str, seller: Citizen, buyer: Citizen, amount: int) -> dict:
    return {"type": "landchain.transfer-intent/v1", "intentId": uuid.uuid4().hex, "surveyNo": survey,
            "sellerRef": seller.ref, "buyerRef": buyer.ref, "considerationINR": amount,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())}


class Demo:
    def __init__(self):
        self.gw: dict[str, Gateway] = {}

    def g(self, msp: str, user: str) -> Gateway:
        key = f"{msp}/{user}"
        if key not in self.gw:
            self.gw[key] = Gateway.for_user(msp, user)
        return self.gw[key]

    async def tx(self, msp: str, user: str, fn: str, *args: str) -> dict | None:
        print(f"  {D}[{ORG[msp]:<17} {user:<10}]{X} {fn} ", end="", flush=True)
        try:
            res = await self.g(msp, user).submit("landrec", fn, *args)
        except Exception as exc:
            print(f"{R}-> FAILED{X}\n     {exc}")
            raise SystemExit(1)
        print(f"{G}-> committed in block {res.block} (endorsed by {len(res.endorsers)} peers){X}")
        return res.json()

    async def denied(self, msp: str, user: str, fn: str, *args: str) -> None:
        print(f"  {D}[{ORG[msp]:<17} {user:<10}]{X} {fn} ", end="", flush=True)
        try:
            await self.g(msp, user).submit("landrec", fn, *args)
        except ChaincodeError as exc:
            print(f"{Y}-> REFUSED{X}\n     {Y}{exc.message}{X}")
            return
        print(f"{R}-> ACCEPTED -- this should have been refused!{X}")
        raise SystemExit(1)

    async def q(self, msp: str, user: str, fn: str, *args: str):
        return await self.g(msp, user).evaluate_json("landrec", fn, *args)

    async def close(self) -> None:
        for g in self.gw.values():
            await g.close()

    async def boundary(self, village: str, area: float) -> str:
        """The next free plot in the village layout, worked out from the boundaries already on the ledger."""
        rings = [p["boundary"]["coordinates"][0] for p in await self.q("Org1MSP", "registrar1", "ListParcels")
                 if p.get("boundary") and p["village"].lower() == village.lower()]
        return json.dumps(layout.next_free_plot(village, area, rings))

    async def register(self, survey: str, village: str, area: int, owner: Citizen, quiet: bool = False,
                       boundary: str | None = None) -> dict | None:
        run = self.tx if not quiet else self._quiet
        return await run("Org1MSP", "registrar1", "RegisterParcel", survey, village, "Hanamkonda", str(area), owner.name,
                         owner.ref, boundary or await self.boundary(village, area))

    async def approve_quietly(self, app: str, holder: Citizen, docs: list[dict], area: int) -> None:
        await self._quiet("Org1MSP", "registrar1", "ForwardToRevenue", app)
        hs = [sha256_hex(dl_fetch("Revenue Dept (Org2)", holder, x["docType"], x["docId"])["content"].encode()) for x in docs]
        await self._quiet("Org2MSP", "verifier1", "VerifyDocuments", app, json.dumps(hs))
        await self._quiet("Org2MSP", "surveyor1", "RecordSurvey", app, str(area), "as recorded")
        await self._quiet("Org2MSP", "verifier1", "ForwardToCollectorate", app)
        await self._quiet("Org3MSP", "collector1", "ApproveTransfer", app, "Approved")

    # -- one complete filing up to the Revenue handoff ---------------------------

    async def file_transfer(self, app_id: str, survey: str, village: str, area: int, seller: Citizen, buyer: Citizen,
                            amount: int, deed: dict, ec: dict | None, quiet: bool = False) -> None:
        consent = intent(survey, seller, buyer, amount)
        envelope = json.dumps({"intent": consent, "seller": seller.sign(consent), "buyer": buyer.sign(consent)})
        run = self.tx if not quiet else self._quiet
        await run("Org1MSP", "clerk1", "SubmitTransferApplication", app_id, survey, seller.ref, buyer.name,
                  buyer.ref, str(amount), envelope)
        for doc in [d for d in (deed, ec) if d]:
            fetched = dl_fetch("Registration Dept (Org1)", seller, doc["docType"], doc["docId"])
            await run("Org1MSP", "clerk1", "AnchorDocument", app_id, fetched["contentSha256"], fetched["docId"],
                      doc["docType"], SRO)

    async def _quiet(self, msp: str, user: str, fn: str, *args: str):
        return (await self.g(msp, user).submit("landrec", fn, *args)).json()


def agent_up() -> bool:
    try:
        return httpx.get(f"{AGENT}/health", timeout=2).status_code == 200
    except Exception:
        return False


async def wait_for_report(app_id: str, timeout: float = 300, quiet: bool = False) -> dict | None:
    deadline = time.monotonic() + timeout
    shown = 0
    async with httpx.AsyncClient(timeout=10) as http:
        while time.monotonic() < deadline:
            runs = (await http.get(f"{AGENT}/runs", params={"appId": app_id})).json()["runs"]
            if runs:
                run = (await http.get(f"{AGENT}/runs/{runs[0]['runId']}")).json()
                for step in ([] if quiet else run.get("steps", [])[shown:]):
                    if step["status"] == "running":
                        break
                    mark = {"ok": G + "ok  ", "pass": G + "ok  ", "warn": Y + "warn", "fail": R + "FAIL",
                            "error": R + "ERR ", "info": D + "    "}.get(step["status"], "    ")
                    kind = {"tool": "tool ", "check": "check", "decision": "rule ", "llm": "model",
                            "sign": "sign ", "anchor": "chain"}.get(step["kind"], step["kind"])
                    print(f"    {D}{kind}{X} {mark}{X} {step['label']}")
                    shown += 1
                if run["status"] in ("anchored", "failed", "skipped"):
                    return run
            await asyncio.sleep(0.5)
    return None


def gpg_verify(report: bytes, signature: str, public_key: str) -> str | None:
    if not shutil.which("gpg"):
        return None
    home = Path(tempfile.mkdtemp(prefix="lc-gpg-"))
    os.chmod(home, 0o700)
    env = {**os.environ, "GNUPGHOME": str(home)}
    (home / "key.asc").write_text(public_key)
    (home / "report.json").write_bytes(report)
    (home / "report.json.asc").write_text(signature)
    subprocess.run(["gpg", "--batch", "--quiet", "--import", str(home / "key.asc")], env=env, capture_output=True)
    out = subprocess.run(["gpg", "--batch", "--verify", str(home / "report.json.asc"), str(home / "report.json")],
                         env=env, capture_output=True, text=True)
    shutil.rmtree(home, ignore_errors=True)
    line = next((ln for ln in out.stderr.splitlines() if "signature" in ln.lower() and ("Good" in ln or "BAD" in ln)), "")
    return line.replace("gpg: ", "").strip() or out.stderr.strip().splitlines()[-1]


async def show_report(run: dict) -> None:
    if not run or run["status"] != "anchored":
        info(f"{R}validator run did not complete: {run and run.get('status')} {run and run.get('error')}{X}")
        return
    v = run["verdict"]
    colour = {"clear": G, "attention": Y, "flagged": R}[v["status"]]
    info(f"verdict {colour}{B}{v['status'].upper()}{X}  ({v['failed']} failed, {v['attention']} attention, "
         f"{v['passed']} passed, {v['skipped']} skipped)  confidence {v['confidence']}")
    exp = run["explanation"]["officer"]
    info(f"{D}explanation ({exp['engine']}{', ' + exp['model'] if exp.get('model') and exp['engine'] == 'omlx' else ''}):{X}")
    for line in _wrap(exp["text"], 92):
        info(f"  {line}")
    async with httpx.AsyncClient(timeout=10) as http:
        report = (await http.get(f"{AGENT}/runs/{run['runId']}/report.json")).content
        sig = (await http.get(f"{AGENT}/runs/{run['runId']}/report.json.asc")).text
        pub = (await http.get(f"{AGENT}/public-key.asc")).text
    info(f"report {run['report']['reportId']}  sha256 {run['report']['sha256'][:16]}...  CID {run['report']['cid'][:20]}...")
    info(f"anchored in block {run['anchor']['block']} by agent1 (Org2MSP, role=agent)")
    v1 = verify_detached(pub, report, sig)
    info(f"OpenPGP signature (python verifier): {G if v1.valid else R}{v1.reason}{X}  key {v1.fingerprint}")
    g = gpg_verify(report, sig, pub)
    if g:
        info(f"OpenPGP signature (GnuPG):           {G if 'Good' in g else R}{g}{X}")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + ([cur] if cur else [])


async def main(seed: int) -> None:
    run = time.strftime("%H%M%S")
    demo = Demo()
    print(f"\n{B}{BL}{'=' * 66}\n  LandChain -- automated land transfer  (run {run})\n{'=' * 66}{X}")
    try:
        httpx.get(f"{DL}/health", timeout=3).raise_for_status()
    except Exception:
        print(f"  {R}DigiLocker is not reachable at {DL}. Run ./startup.sh --no-demo first.{X}")
        raise SystemExit(1)
    has_agent = agent_up()

    # -----------------------------------------------------------------------
    act("Act 1 · Identities -- what each X.509 certificate tells the chaincode")
    for msp, user in [("Org1MSP", "registrar1"), ("Org1MSP", "clerk1"), ("Org1MSP", "portal1"),
                      ("Org2MSP", "verifier1"), ("Org2MSP", "surveyor1"), ("Org2MSP", "agent1"),
                      ("Org3MSP", "collector1")]:
        w = await demo.q(msp, user, "WhoAmI")
        print(f"    {w['name']:<11} {w['mspId']:<8} role={w['role']:<10} dept={w['dept']}")
    note("role and dept are X.509 attributes stamped by each department's own CA at enrollment.")

    # -----------------------------------------------------------------------
    act("Act 2 · Citizens -- DigiLocker accounts and self-sovereign keys (did:key)")
    seller = Citizen("Ramesh Kumar", "9876500001", "4321")
    buyer = Citizen("Lakshmi Devi", "9876500002", "8765")
    for c in (seller, buyer):
        info(f"{c.name:<13} {c.dl_id}  ownerRef {c.ref[:16]}...  {c.did[:32]}...")
        await demo.tx("Org1MSP", "portal1", "RegisterCitizen", c.ref, c.did)
    note("Registration binds each citizen's DID to their ownerRef after DigiLocker KYC; no Aadhaar on the ledger.")
    survey = f"SY-{run}-42/1A"
    deed = issue(seller, "SALEDEED", f"Sale deed for {survey}",
                 deed_text(survey, "Kazipet", 2400, "Ramesh Kumar", "Lakshmi Devi", "45,00,000"))
    ec = issue(seller, "ENCUMBRANCE", f"Encumbrance certificate for {survey}", ec_text(survey))
    info(f"Issued to the seller's locker: SALEDEED {deed['docId']}  sha256 {deed['contentSha256'][:16]}...")
    info(f"                               ENCUMBRANCE {ec['docId']}  sha256 {ec['contentSha256'][:16]}...")

    # -----------------------------------------------------------------------
    act("Act 3 · Org1 Registration -- parcel, DID-signed transfer, anchored documents, handoff")
    app = f"APP-{run}-001"
    for i in range(seed):                     # neighbours with approved sales: comparables for the price checks
        await seed_transfer(demo, run, i)
    await demo.register(survey, "Kazipet", 2400, seller)
    note("The boundary (a GeoJSON polygon) is on the ledger; the gateway projects it into the geospatial database.")
    note("Seller and buyer each sign the same transfer intent with their own keys; the chaincode verifies both:")
    await demo.file_transfer(app, survey, "Kazipet", 2400, seller, buyer, 4500000, deed, ec)
    await demo.tx("Org1MSP", "registrar1", "ForwardToRevenue", app)
    note("Org1 -> Org2 handoff: the HandoffToRevenue event wakes the Agentic AI Validator.")

    # -----------------------------------------------------------------------
    act("Act 4 · Agentic AI Validator -- investigates, explains, signs, anchors (evidence only)")
    if has_agent:
        await show_report(await wait_for_report(app))
        status = (await demo.q("Org2MSP", "verifier1", "ReadApplication", app))["status"]
        note(f"Application status is still {status}: the report is evidence, not a decision.")
    else:
        info(f"{Y}the validator is not running (./landchain.sh restart agent); skipping{X}")

    # -----------------------------------------------------------------------
    act("Act 5 · Org2 Revenue -- independent verification and field survey")
    note("The verifier fetches the documents through its OWN consent request and hashes them locally:")
    filed = (await demo.q("Org2MSP", "verifier1", "ReadApplication", app))["documents"]
    hashes = [sha256_hex(dl_fetch("Revenue Dept (Org2)", seller, d["documentType"], d["docId"])["content"].encode())
              for d in filed]
    await demo.tx("Org2MSP", "verifier1", "VerifyDocuments", app, json.dumps(hashes))
    await demo.tx("Org2MSP", "surveyor1", "RecordSurvey", app, "2412", "N: 30ft road | E: SY-43 | S: canal | W: SY-41")
    await demo.tx("Org2MSP", "verifier1", "ForwardToCollectorate", app)
    a = await demo.q("Org2MSP", "verifier1", "ReadApplication", app)
    info(f"verification: {a['verification']['result']} ({a['verification']['documentsChecked']} documents)   "
         f"survey: deviation {a['survey']['deviationPct']}% (tolerance {a['survey']['tolerancePct']}%)")

    # -----------------------------------------------------------------------
    act("Act 6 · Org3 Collectorate -- the human decision")
    await demo.tx("Org3MSP", "collector1", "ApproveTransfer", app, "Documents verified, survey within tolerance, "
                  "validator report clear. Approved.")
    p = await demo.q("Org3MSP", "collector1", "ReadParcel", survey)
    info(f"Parcel {p['surveyNo']}: owner is now {p['ownerName']} (was {p['lastTransfer']['fromName']}; via {p['lastTransfer']['appId']})")
    note("A year on, the new owner sells again (the chain of ownership grows by one link):")
    third = Citizen("Venkat Rao", "9876500003", "2468")
    await demo._quiet("Org1MSP", "portal1", "RegisterCitizen", third.ref, third.did)
    deed_b = issue(buyer, "SALEDEED", f"Sale deed for {survey}",
                   deed_text(survey, "Kazipet", 2400, "Lakshmi Devi", "Venkat Rao", "52,00,000"))
    ec_b = issue(buyer, "ENCUMBRANCE", f"Encumbrance certificate for {survey}", ec_text(survey, "Lakshmi Devi"))
    app_b = f"APP-{run}-007"
    await demo.file_transfer(app_b, survey, "Kazipet", 2400, buyer, third, 5200000, deed_b, ec_b, quiet=True)
    await demo.approve_quietly(app_b, buyer, [deed_b, ec_b], 2400)
    if has_agent:
        await wait_for_report(app_b, quiet=True)
    info(f"{app_b}: Lakshmi Devi -> Venkat Rao, approved")

    # -----------------------------------------------------------------------
    act("Act 7 · Attacks and edge cases -- what the network refuses, what the validator catches")
    note("1. Wrong department: a Registration clerk tries to approve a transfer.")
    await demo.denied("Org1MSP", "clerk1", "ApproveTransfer", app, "self-approval")

    note("2. The validator tries to decide. Identity, not authority:")
    await demo.denied("Org2MSP", "agent1", "ApproveTransfer", app, "approved by the AI")
    await demo.denied("Org2MSP", "agent1", "RegisterAgentKey", "agent1", "A" * 40, buyer.did,
                      "-----BEGIN PGP PUBLIC KEY BLOCK-----\nx\n-----END PGP PUBLIC KEY BLOCK-----")

    note("3. Double sale: the previous owner tries to sell the same parcel again.")
    other = Citizen("Someone Else", "9876500009", "1111")
    await demo.tx("Org1MSP", "portal1", "RegisterCitizen", other.ref, other.did)
    i3 = intent(survey, seller, other, 100000)
    await demo.denied("Org1MSP", "clerk1", "SubmitTransferApplication", f"APP-{run}-002", survey, seller.ref,
                      other.name, other.ref, "100000", json.dumps({"intent": i3, "seller": seller.sign(i3), "buyer": other.sign(i3)}))

    note("4. Forged consent: a transfer of the new owner's land, 'signed' with somebody else's key.")
    impostor = Ed25519PrivateKey.generate()
    i4 = intent(survey, buyer, other, 100000)
    forged = {"did": buyer.did, "signature": base64.b64encode(impostor.sign(canonical(i4))).decode()}
    await demo.denied("Org1MSP", "clerk1", "SubmitTransferApplication", f"APP-{run}-003", survey, buyer.ref,
                      other.name, other.ref, "100000", json.dumps({"intent": i4, "seller": forged, "buyer": other.sign(i4)}))

    note("5. Tampering at the source: a deed is altered inside DigiLocker after its fingerprint was anchored.")
    survey2 = f"SY-{run}-77/2B"
    await demo.register(survey2, "Hanamkonda", 1800, buyer)
    deed2 = issue(buyer, "SALEDEED", f"Sale deed for {survey2}",
                  deed_text(survey2, "Hanamkonda", 1800, "Lakshmi Devi", "Ramesh Kumar", "30,00,000"))
    ec2 = issue(buyer, "ENCUMBRANCE", f"Encumbrance certificate for {survey2}", ec_text(survey2))
    app2 = f"APP-{run}-004"
    await demo.file_transfer(app2, survey2, "Hanamkonda", 1800, buyer, seller, 3000000, deed2, ec2)
    t = httpx.post(f"{DL}/admin/tamper", json={"doc_id": deed2["docId"], "find": "1800 sq.m", "replace": "2800 sq.m"}).json()
    note(f"   insider edit: 1800 sq.m -> 2800 sq.m; DigiLocker still lists fingerprint {t['contentSha256'][:12]}..., "
         f"content now hashes to {t['actualSha256'][:12]}...")
    await demo.tx("Org1MSP", "registrar1", "ForwardToRevenue", app2)
    if has_agent:
        note("   the validator investigates before any Revenue officer opens the file:")
        await show_report(await wait_for_report(app2))
    tampered = sha256_hex(dl_fetch("Revenue Dept (Org2)", buyer, "SALEDEED", deed2["docId"])["content"].encode())
    ec_hash = sha256_hex(dl_fetch("Revenue Dept (Org2)", buyer, "ENCUMBRANCE", ec2["docId"])["content"].encode())
    await demo.tx("Org2MSP", "verifier1", "VerifyDocuments", app2, json.dumps([tampered, ec_hash]))
    a2 = await demo.q("Org2MSP", "verifier1", "ReadApplication", app2)
    info(f"status: {a2['status']}   verification: {a2['verification']['result']}   mismatched: "
         f"{', '.join(a2['verification']['mismatchedDocuments'])}")
    await demo.tx("Org3MSP", "collector1", "RejectApplication", app2,
                  "Deed altered at source after anchoring (validator + Revenue) -- referred for investigation")
    p2 = await demo.q("Org3MSP", "collector1", "ReadParcel", survey2)
    info(f"parcel {p2['surveyNo']} released: status {p2['status']}, owner still {p2['ownerName']}")

    note("6. Reused deed: the first transfer's sale deed is anchored to a different file.")
    app3 = f"APP-{run}-005"
    i6 = intent(survey2, buyer, seller, 3000000)
    await demo.tx("Org1MSP", "clerk1", "SubmitTransferApplication", app3, survey2, buyer.ref, seller.name, seller.ref,
                  "3000000", json.dumps({"intent": i6, "seller": buyer.sign(i6), "buyer": seller.sign(i6)}))
    await demo.denied("Org1MSP", "clerk1", "AnchorDocument", app3, deed["contentSha256"], deed["docId"], "SALEDEED", SRO)

    note("7. Not an attack, but worth a human look: the deed spells the purchaser 'Laxmi' for 'Lakshmi'.")
    survey3 = f"SY-{run}-12/4C"
    await demo.register(survey3, "Kazipet", 900, seller)
    deed3 = issue(seller, "SALEDEED", f"Sale deed for {survey3}",
                  deed_text(survey3, "Kazipet", 900, "Ramesh Kumar", "Laxmi Devi", "16,00,000"))
    app4 = f"APP-{run}-006"
    await demo.file_transfer(app4, survey3, "Kazipet", 900, seller, buyer, 1600000, deed3, None)
    await demo.tx("Org1MSP", "registrar1", "ForwardToRevenue", app4)
    if has_agent:
        await show_report(await wait_for_report(app4))

    note("8. The same ground registered twice: a new survey number whose boundary overlaps the first parcel.")
    survey4 = f"SY-{run}-42/1A-X"
    first = await demo.q("Org1MSP", "registrar1", "ReadParcel", survey)
    ring = first["boundary"]["coordinates"][0]
    shifted = [[x + (ring[1][0] - ring[0][0]) * 0.4, y + (ring[1][1] - ring[0][1]) * 0.4] for x, y in ring]
    from chaincode.landrec import geometry
    area4 = round(geometry.area_sqm(shifted))
    await demo.register(survey4, "Kazipet", area4, other, boundary=json.dumps({"type": "Polygon", "coordinates": [shifted]}))
    note("   the ledger cannot see overlaps (it is a key-value store); the geospatial database can, and the dashboard's "
         "registration form refuses it. Registered here straight through the SDK, then put up for sale:")
    deed4 = issue(other, "SALEDEED", f"Sale deed for {survey4}",
                  deed_text(survey4, "Kazipet", area4, "Someone Else", "Ramesh Kumar", "9,00,000"))
    ec4 = issue(other, "ENCUMBRANCE", f"Encumbrance certificate for {survey4}", ec_text(survey4))
    app5 = f"APP-{run}-008"
    await demo.file_transfer(app5, survey4, "Kazipet", area4, other, seller, 900000, deed4, ec4, quiet=True)
    await demo.tx("Org1MSP", "registrar1", "ForwardToRevenue", app5)
    if has_agent:
        await show_report(await wait_for_report(app5))

    # -----------------------------------------------------------------------
    act("Act 8 · Audit -- what anyone on the channel can verify")
    r = await demo.q("Org3MSP", "collector1", "VerifyDocument", deed["contentSha256"])
    info(f"genuine deed   -> anchored={r['anchored']}  application {r.get('appId')}  by {r.get('anchoredBy')}")
    r = await demo.q("Org3MSP", "collector1", "VerifyDocument", tampered)
    info(f"tampered copy  -> anchored={r['anchored']}")
    a = await demo.q("Org3MSP", "collector1", "ReadApplication", app)
    for v in a.get("validations", []):
        r = await demo.q("Org3MSP", "collector1", "VerifyValidationReport", v["sha256"])
        info(f"validator report {v['sha256'][:16]}... -> anchored={r['anchored']} verdict={r['verdict']} "
             f"key {r['pgpFingerprint'][:16]}...")
    note(f"Chain of ownership of {survey}, derived on the ledger from every committed version (OwnershipChain):")
    chain = await demo.q("Org2MSP", "verifier1", "OwnershipChain", survey)
    for link in chain["links"]:
        how = "registered" if link["acquired"] == "registration" else f"bought via {link['appId']}"
        info(f"{link['seq']}. {link['ownerName']:<14} {how:<26} {link['since'][:10]} -> "
             f"{(link['until'] or 'today')[:10]}  tx {link['sinceTxId'][:12]}...")
    note(f"Transfer file {app} -- who did what (the agent's line is evidence, the collector's is the decision):")
    for t in a["trail"]:
        info(f"{t['at']}  {t['org']:<8} {t['by']:<11} ({t['role']:<9}) {t['action']}")

    note(f"Signed transfer records: anyone can download the public copy of {app}; the parties get their own:")
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            rec = (await http.get(f"{GATEWAY}/api/public/records/{app}")).text
            key = (await http.get(f"{GATEWAY}/api/public/records-key")).json()
        v, _ = verify_cleartext(key["publicKey"], rec)
        info(f"public record: {len(rec.splitlines())} lines, OpenPGP {G if v.valid else R}{v.reason}{X}, "
             f"Records Office key {v.fingerprint}" + (f" (attested by {key['onLedger']['attestedBy']})" if key.get("onLedger") else ""))
    except Exception as exc:
        info(f"{Y}the gateway is not reachable for records ({exc}){X}")

    await demo.close()
    print(f"\n{B}{BL}{'=' * 66}\n  Done -- every step above is a committed, 3-way-endorsed transaction\n{'=' * 66}{X}")
    web = config.node("web")["port"]
    info(f"Dashboards: http://localhost:{web}   (officials: clerk1 / verifier1 / collector1, password = username)")


async def seed_transfer(demo: Demo, run: str, i: int) -> None:
    """An extra, fully approved transfer in Kazipet (comparables for the valuation checks)."""
    if i == 0:
        act("Neighbours · approved sales next door (comparables for the price checks)")
    s, b = Citizen(f"Seller {run}-{i}", f"98000{i:05d}", "1000"), Citizen(f"Buyer {run}-{i}", f"98100{i:05d}", "2000")
    for c in (s, b):
        await demo._quiet("Org1MSP", "portal1", "RegisterCitizen", c.ref, c.did)
    survey, area = f"SY-{run}-S{i}", 1000 + 200 * i
    amount = area * (1850 + 60 * i)                       # about Rs. 1,900 per sq.m, like the first sale
    await demo.register(survey, "Kazipet", area, s, quiet=True)
    d = issue(s, "SALEDEED", "seed deed", deed_text(survey, "Kazipet", area, s.name, b.name, f"{amount:,}"))
    e = issue(s, "ENCUMBRANCE", "seed ec", ec_text(survey))
    app = f"APP-{run}-S{i}"
    await demo.file_transfer(app, survey, "Kazipet", area, s, b, amount, d, e, quiet=True)
    await demo.approve_quietly(app, s, [d, e], area)
    info(f"{app}: {survey}, {area} sq.m for Rs. {amount:,}, approved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LandChain automated transactions")
    parser.add_argument("--seed", type=int, default=3, help="approved neighbouring sales to create first (comparables)")
    asyncio.run(main(parser.parse_args().seed))
