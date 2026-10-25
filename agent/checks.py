"""
The validator's checks: fixed, readable rules over the evidence it gathered.

Each check returns {id, label, status, detail, evidence[]} where status is
  pass | warn | fail | skipped (not enough data to judge; not counted)

Some checks investigate further before deciding -- that is the "agentic"
part: the next tool call depends on what the last one found.

  document_integrity  mismatch -> fetch a second copy + the issuer's original
                      fingerprint + ask the ledger whether the altered copy is
                      anchored anywhere -> say WHICH kind of tampering it is
  party_names         vendor != owner -> look through the parcel's ownership
                      history for the deed's vendor
  prior_flags         earlier flagged/rejected files -> read them to report
                      the officers' recorded reasons

Thresholds live in settings.py so they are easy to find and change.
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
import statistics
from dataclasses import dataclass
from typing import Any

from . import guard, settings
from .tools import Tools, Trace, sha256_text

HONORIFICS = {"sri", "smt", "shri", "shrimati", "mr", "mrs", "ms", "dr", "kumari", "late", "thiru", "km"}
UNIT_TO_SQM = {"sqm": 1.0, "sq.m": 1.0, "sq m": 1.0, "square metres": 1.0, "square meters": 1.0,
               "sq.yd": 0.836127, "sq yd": 0.836127, "square yards": 0.836127, "acres": 4046.856, "acre": 4046.856,
               "guntas": 101.17, "gunta": 101.17}


@dataclass
class Evidence:
    app: dict[str, Any]
    parcel: dict[str, Any]
    history: list[dict[str, Any]]
    apps: list[dict[str, Any]]
    parcels: list[dict[str, Any]]
    docs: dict[str, dict[str, Any]]
    computed: dict[str, str]
    holders: dict[str, dict[str, Any] | None]
    citizens: dict[str, dict[str, Any] | None]
    purpose: str


def result(cid: str, label: str, status: str, detail: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return {"id": cid, "label": label, "status": status, "detail": detail, "evidence": evidence or []}


def parse_time(ts: str | None) -> dt.datetime | None:
    if not ts:
        return None
    try:
        v = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Reading the documents
# ---------------------------------------------------------------------------

def _field(text: str, *names: str) -> str | None:
    for name in names:
        m = re.search(rf"^\s*{name}\s*:\s*(.+?)\s*$", text, re.I | re.M)
        if m:
            return m.group(1)
    return None


def parse_inr(text: str | None) -> int | None:
    if not text:
        return None
    m = re.search(r"(?:rs\.?|inr|₹)?\s*([\d,]+)(?:\.\d+)?", text, re.I)
    return int(m.group(1).replace(",", "")) if m else None


def parse_deed(content: str) -> dict[str, Any]:
    survey = _field(content, "Survey No", "Survey Number", "Sy. No")
    extent_text = _field(content, "Extent", "Area")
    extent = None
    if extent_text:
        m = re.match(r"([\d.,]+)\s*(.*)$", extent_text)
        if m:
            unit = m.group(2).strip().lower().rstrip(".") or "sq.m"
            factor = next((f for u, f in UNIT_TO_SQM.items() if unit.startswith(u.rstrip("."))), None)
            if factor:
                extent = round(float(m.group(1).replace(",", "")) * factor, 2)
    return {"surveyNo": survey.split(",")[0].strip() if survey else None, "extentSqm": extent,
            "extentText": extent_text, "vendor": _field(content, "Vendor", "Seller"),
            "purchaser": _field(content, "Purchaser", "Buyer", "Vendee"),
            "considerationINR": parse_inr(_field(content, "Consideration"))}


def parse_ec(content: str) -> dict[str, Any]:
    survey = _field(content, "Survey No", "Survey Number")
    period = re.search(r"Period searched\s*:\s*(\d{4})\s*[-–]\s*(\d{4})", content, re.I)
    res = _field(content, "Result")
    return {"surveyNo": survey.split(",")[0].strip() if survey else None,
            "periodFrom": int(period.group(1)) if period else None,
            "periodTo": int(period.group(2)) if period else None,
            "result": res, "nil": bool(res and re.search(r"\bnil\b", res, re.I))}


def norm_tokens(name: str | None) -> list[str]:
    words = re.findall(r"[a-z]+", (name or "").lower())
    return [w for w in words if w not in HONORIFICS]


def name_match(a: str | None, b: str | None) -> str:
    """exact | near | different -- tolerant of order, initials and transliteration."""
    ta, tb = norm_tokens(a), norm_tokens(b)
    if not ta or not tb:
        return "different"
    if ta == tb or sorted(ta) == sorted(tb):
        return "exact"
    if len(ta) == len(tb) and all(x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))
                                  for x, y in zip(ta, tb)):
        return "near"
    if (set(ta) <= set(tb) or set(tb) <= set(ta)) and ta[-1] == tb[-1]:
        return "near"
    if difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio() >= 0.80:
        return "near"
    return "different"


def _docs_of_type(ev: Evidence, doc_type: str) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    return [(d, ev.docs.get(d["docId"])) for d in ev.app["documents"] if d["documentType"].upper() == doc_type]


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

async def check_document_integrity(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "Document fingerprints match the ledger"
    good, problems, evidence = [], [], []
    for d in ev.app["documents"]:
        fetched = ev.docs.get(d["docId"])
        name = f"{d['documentType']} {d['docId']}"
        if fetched is None:
            problems.append(f"{name} could not be retrieved from DigiLocker")
            continue
        actual = ev.computed[d["docId"]]
        evidence.append(f"{name}: anchored {d['sha256'][:16]}..., computed {actual[:16]}...")
        if actual == d["sha256"]:
            good.append(name)
            continue
        await trace.note("decision", "policy.second-source", f"Fingerprint mismatch on {name}: investigate first",
                         "A mismatch can be a corrupted copy or a document that changed at its source. The policy "
                         "requires a second copy, the issuer's own fingerprint, and a ledger lookup before reporting.",
                         status="warn")
        again = await tools.fetch_document(d["docId"], ev.purpose, "Follow-up: an independent second copy rules out "
                                           "corruption in transit", label=f"Re-fetch {d['docId']} (second copy)")
        second = sha256_text(again["content"])
        issuer_fp = again["contentSha256"]
        other = await tools.verify_document(second, "Follow-up: is the altered version anchored to any application?")
        evidence.append(f"{name}: second copy {second[:16]}..., issuer's issue-time fingerprint {issuer_fp[:16]}..., "
                        f"altered version anchored anywhere: {'yes' if other.get('anchored') else 'no'}")
        if second == d["sha256"]:
            problems.append(f"{name}: the first copy was corrupted in transit; a second copy matches (transient)")
        elif issuer_fp == d["sha256"]:
            problems.append(f"{name} was altered at the source after it was anchored: DigiLocker's own issue-time "
                            "fingerprint still equals the ledger anchor, but the stored content no longer hashes to it")
        else:
            problems.append(f"{name} was re-issued with different content after its fingerprint was anchored")
    if not problems:
        return result("document_integrity", label, "pass",
                      f"All {len(good)} documents hash to the fingerprints Registration anchored.", evidence)
    transient_only = all("transient" in p for p in problems)
    return result("document_integrity", label, "warn" if transient_only else "fail", "; ".join(problems), evidence)


async def check_anchor_uniqueness(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "Each document is anchored only to this application"
    bad, evidence = [], []
    for d in ev.app["documents"]:
        r = await tools.verify_document(d["sha256"], "Confirm the ledger anchor points at this application")
        evidence.append(f"{d['docId']} -> anchored to {r.get('appId')}")
        if not r.get("anchored") or r.get("appId") != ev.app["appId"]:
            bad.append(d["docId"])
    if bad:
        return result("anchor_uniqueness", label, "fail", f"Anchor mismatch for {', '.join(bad)}.", evidence)
    return result("anchor_uniqueness", label, "pass",
                  "Every fingerprint is anchored to this application and to no other.", evidence)


async def check_required_documents(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "Sale deed and encumbrance certificate are on file"
    types = [d["documentType"].upper() for d in ev.app["documents"]]
    evidence = [f"anchored: {', '.join(types) or 'none'}"]
    if "SALEDEED" not in types:
        return result("required_documents", label, "fail", "No sale deed is anchored to this application.", evidence)
    if "ENCUMBRANCE" not in types:
        return result("required_documents", label, "warn",
                      "No encumbrance certificate is on file, so charges on the land were not checked.", evidence)
    return result("required_documents", label, "pass", "Both the sale deed and the encumbrance certificate are on file.",
                  evidence)


async def check_deed_parcel(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "The sale deed describes this parcel"
    deeds = _docs_of_type(ev, "SALEDEED")
    if not deeds or deeds[0][1] is None:
        return result("deed_parcel", label, "skipped", "No readable sale deed.")
    deed = parse_deed(deeds[0][1]["content"])
    recorded = ev.parcel["areaSqm"]
    evidence = [f"deed survey no: {deed['surveyNo']}", f"application survey no: {ev.app['surveyNo']}",
                f"deed extent: {deed['extentText']}", f"recorded area: {recorded} sq.m"]
    if (deed["surveyNo"] or "").replace(" ", "").upper() != ev.app["surveyNo"].replace(" ", "").upper():
        return result("deed_parcel", label, "fail",
                      f"The deed is for survey number {deed['surveyNo']}, not {ev.app['surveyNo']}.", evidence)
    if deed["extentSqm"] is None:
        return result("deed_parcel", label, "warn", "The deed's extent could not be read.", evidence)
    dev = round(abs(deed["extentSqm"] - recorded) / recorded * 100, 2)
    evidence.append(f"difference: {dev}%")
    if dev > settings.EXTENT_FAIL_PCT:
        return result("deed_parcel", label, "fail",
                      f"The deed's extent ({deed['extentSqm']} sq.m) differs from the recorded {recorded} sq.m by {dev}%.",
                      evidence)
    if dev > settings.EXTENT_WARN_PCT:
        return result("deed_parcel", label, "warn",
                      f"The deed's extent ({deed['extentSqm']} sq.m) differs from the recorded {recorded} sq.m by {dev}%.",
                      evidence)
    return result("deed_parcel", label, "pass",
                  f"The deed names survey number {deed['surveyNo']} with an extent of {deed['extentSqm']} sq.m "
                  f"(recorded: {recorded} sq.m).", evidence)


async def check_party_names(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "Names on the deed match the parties"
    deeds = _docs_of_type(ev, "SALEDEED")
    if not deeds or deeds[0][1] is None:
        return result("party_names", label, "skipped", "No readable sale deed.")
    deed = parse_deed(deeds[0][1]["content"])
    seller_dl = (ev.holders.get("seller") or {}).get("name")
    buyer_dl = (ev.holders.get("buyer") or {}).get("name")
    pairs = [
        ("deed vendor", deed["vendor"], "registered owner", ev.parcel["ownerName"]),
        ("deed vendor", deed["vendor"], "seller's DigiLocker name", seller_dl),
        ("deed purchaser", deed["purchaser"], "application buyer", ev.app["buyerName"]),
        ("deed purchaser", deed["purchaser"], "buyer's DigiLocker name", buyer_dl),
    ]
    evidence, worst, notes = [], "exact", []
    for la, a, lb, b in pairs:
        m = name_match(a, b)
        evidence.append(f"{la} '{a}' vs {lb} '{b}': {m}")
        if m == "different":
            worst = "different"
            notes.append(f"{la} '{a}' does not match the {lb} '{b}'")
        elif m == "near" and worst == "exact":
            worst = "near"
            notes.append(f"{la} '{a}' only approximately matches the {lb} '{b}'")
    if worst == "different" and name_match(deed["vendor"], ev.parcel["ownerName"]) == "different":
        await trace.note("decision", "policy.vendor-history", "Vendor is not the current owner: check earlier owners",
                         "A deed that names a former owner as vendor can mean the deed predates the last transfer.",
                         status="warn")
        hist = await tools.parcel_history(ev.app["surveyNo"], "Follow-up: does the deed's vendor appear among "
                                          "earlier owners of this parcel?")
        owners = [(v.get("value") or {}).get("ownerName") for v in hist]
        earlier = [o for o in owners if o and name_match(o, deed["vendor"]) != "different"]
        if earlier:
            notes.append(f"'{deed['vendor']}' is a former owner of this parcel; the deed may predate the last transfer")
        evidence.append(f"owners over time: {', '.join(dict.fromkeys(o for o in owners if o))}")
    if worst == "exact":
        return result("party_names", label, "pass",
                      f"Vendor '{deed['vendor']}' and purchaser '{deed['purchaser']}' match the ledger and the "
                      "parties' DigiLocker names.", evidence)
    return result("party_names", label, "fail" if worst == "different" else "warn", "; ".join(notes) + ".", evidence)


async def check_consideration(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "Consideration on the deed matches the application"
    deeds = _docs_of_type(ev, "SALEDEED")
    if not deeds or deeds[0][1] is None:
        return result("consideration", label, "skipped", "No readable sale deed.")
    deed_amt = parse_deed(deeds[0][1]["content"])["considerationINR"]
    app_amt = ev.app["considerationINR"]
    evidence = [f"deed: {deed_amt}", f"application: {app_amt}"]
    if deed_amt is None:
        return result("consideration", label, "warn", "The deed's consideration could not be read.", evidence)
    if int(deed_amt) != int(app_amt):
        return result("consideration", label, "fail",
                      f"The deed states Rs. {deed_amt} but the application (and the parties' signed intent) "
                      f"states Rs. {app_amt}.", evidence)
    return result("consideration", label, "pass", f"Both state Rs. {app_amt}.", evidence)


async def check_documents_from_seller(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "Documents come from the seller's own DigiLocker"
    bad, evidence = [], []
    for d in ev.app["documents"]:
        f = ev.docs.get(d["docId"])
        if f is None:
            continue
        own = f["holder"]["ownerRef"] == ev.app["sellerRef"]
        evidence.append(f"{d['docId']}: in the locker of {f['holder']['name']} ({'seller' if own else 'NOT the seller'})")
        if not own:
            bad.append(f"{d['documentType']} {d['docId']} is held by {f['holder']['name']}")
    if bad:
        return result("documents_from_seller", label, "fail", "; ".join(bad) + ".", evidence)
    return result("documents_from_seller", label, "pass",
                  "Every document was issued into the seller's own DigiLocker.", evidence)


async def check_encumbrance(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "Encumbrance certificate shows no charges"
    ecs = _docs_of_type(ev, "ENCUMBRANCE")
    if not ecs or ecs[0][1] is None:
        return result("encumbrance", label, "skipped", "No encumbrance certificate on file.")
    ec = parse_ec(ecs[0][1]["content"])
    created = parse_time(ev.app["createdAt"])
    year = created.year if created else dt.datetime.now(dt.timezone.utc).year
    evidence = [f"survey no: {ec['surveyNo']}", f"period: {ec['periodFrom']}-{ec['periodTo']}", f"result: {ec['result']}"]
    if (ec["surveyNo"] or "").replace(" ", "").upper() != ev.app["surveyNo"].replace(" ", "").upper():
        return result("encumbrance", label, "fail",
                      f"The certificate covers survey number {ec['surveyNo']}, not {ev.app['surveyNo']}.", evidence)
    if not ec["nil"]:
        return result("encumbrance", label, "fail", f"The certificate records a charge: {ec['result']}.", evidence)
    if ec["periodTo"] and year - ec["periodTo"] > settings.EC_MAX_AGE_YEARS:
        return result("encumbrance", label, "warn",
                      f"NIL encumbrance, but the search period ends in {ec['periodTo']}; it should reach {year}.", evidence)
    return result("encumbrance", label, "pass",
                  f"NIL encumbrance for {ec['periodFrom']}-{ec['periodTo']}.", evidence)


async def check_consent_signatures(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "Both parties' consents are DID-signed"
    consent = ev.app.get("consent")
    if not consent:
        return result("consent_signatures", label, "fail", "The application carries no signed consent.")
    evidence = [f"intent {consent['intentId']} (SHA-256 {consent['intentSha256'][:16]}...)"]
    problems = []
    for party in ("seller", "buyer"):
        rec = ev.citizens.get(party)
        signed = consent[f"{party}Did"]
        evidence.append(f"{party} signed with {signed[:28]}...")
        if not rec:
            problems.append(f"the {party} has no registered DID")
        elif rec["did"] != signed:
            problems.append(f"the {party}'s signature is from a DID other than their registered one")
    if problems:
        return result("consent_signatures", label, "fail", "; ".join(problems) + ".", evidence)
    return result("consent_signatures", label, "pass",
                  "The seller and the buyer each signed the same transfer intent with their registered did:key; "
                  "the chaincode verified both signatures when the file was opened.", evidence)


async def check_resale_velocity(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "No rapid re-sale pattern"
    created = parse_time(ev.app["createdAt"])
    transfers = []
    for v in ev.history:
        lt = (v.get("value") or {}).get("lastTransfer")
        if lt and lt.get("appId") not in [t["appId"] for t in transfers]:
            transfers.append(lt)
    evidence = [f"earlier transfers: {len(transfers)}"] + [f"{t['appId']} on {t['at'][:10]}: {t['fromName']} -> {t['toName']}"
                                                          for t in transfers]
    if not transfers or not created:
        return result("resale_velocity", label, "pass", "No earlier transfer of this parcel is recorded.", evidence)
    last = parse_time(transfers[-1]["at"])
    days = (created - last).days if last else None
    recent_year = [t for t in transfers if (p := parse_time(t["at"])) and (created - p).days <= 365]
    if days is not None and days <= settings.RAPID_RESALE_DAYS:
        return result("resale_velocity", label, "warn",
                      f"This parcel last changed hands {days} days before this application "
                      f"(threshold {settings.RAPID_RESALE_DAYS} days).", evidence)
    if len(recent_year) >= 2:
        return result("resale_velocity", label, "warn",
                      "This would be the third transfer of the parcel within a year.", evidence)
    return result("resale_velocity", label, "pass", f"The last transfer was {days} days earlier.", evidence)


async def check_prior_flags(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "No earlier flagged or rejected files on this parcel or these parties"
    me = ev.app["appId"]
    refs = {ev.app["sellerRef"], ev.app["buyerRef"]}
    hits = [a for a in ev.apps if a["appId"] != me and a["status"] in ("FLAGGED", "REJECTED")
            and (a["surveyNo"] == ev.app["surveyNo"] or a["sellerRef"] in refs or a["buyerRef"] in refs)]
    if not hits:
        return result("prior_flags", label, "pass", "No earlier flagged or rejected applications involve this "
                      "parcel or these parties.")
    await trace.note("decision", "policy.read-prior", f"{len(hits)} earlier problem file(s): read the recorded reasons",
                     "An earlier rejection matters only with its reason; read up to two of them.", status="warn")
    evidence = []
    for a in hits[:2]:
        full = await tools.read_application(a["appId"], "Follow-up: what did the officers record about this earlier file?")
        why = (full.get("decision") or {}).get("remarks") or (full.get("verification") or {}).get("result") or full["status"]
        rel = "same parcel" if a["surveyNo"] == ev.app["surveyNo"] else "same party"
        evidence.append(f"{a['appId']} ({rel}): {full['status']} -- {why}")
    return result("prior_flags", label, "warn",
                  f"{len(hits)} earlier application(s) involving this parcel or these parties were flagged or rejected.",
                  evidence)


async def check_valuation(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    label = "Price is in line with comparable transfers in the district"
    by_survey = {p["surveyNo"]: p for p in ev.parcels}
    district = ev.parcel["district"]
    comps = []
    for a in ev.apps:
        p = by_survey.get(a["surveyNo"])
        if a["appId"] != ev.app["appId"] and a["status"] == "APPROVED" and p and p["district"] == district and p["areaSqm"]:
            comps.append(a["considerationINR"] / p["areaSqm"])
    mine = ev.app["considerationINR"] / ev.parcel["areaSqm"]
    evidence = [f"this transfer: Rs. {round(mine)} per sq.m", f"approved transfers in {district}: {len(comps)}"]
    if len(comps) < settings.VALUATION_MIN_COMPARABLES:
        return result("valuation", label, "skipped",
                      f"Only {len(comps)} approved transfers in {district} so far; at least "
                      f"{settings.VALUATION_MIN_COMPARABLES} are needed to judge the price.", evidence)
    median = statistics.median(comps)
    ratio = mine / median
    evidence.append(f"district median: Rs. {round(median)} per sq.m (ratio {ratio:.2f})")
    if ratio < settings.VALUATION_LOW_RATIO:
        return result("valuation", label, "warn",
                      f"Rs. {round(mine)} per sq.m is {round(ratio * 100)}% of the district median of Rs. "
                      f"{round(median)} per sq.m: possible under-valuation (stamp duty).", evidence)
    if ratio > settings.VALUATION_HIGH_RATIO:
        return result("valuation", label, "warn",
                      f"Rs. {round(mine)} per sq.m is {round(ratio * 100)}% of the district median of Rs. "
                      f"{round(median)} per sq.m: unusually high.", evidence)
    return result("valuation", label, "pass",
                  f"Rs. {round(mine)} per sq.m against a district median of Rs. {round(median)} per sq.m.", evidence)


async def check_document_instructions(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    """A document is evidence, never instructions. Text that tries to steer an AI reader is itself a finding."""
    label = "Documents contain no instructions aimed at an AI reader"
    evidence, scanned = [], 0
    for d in ev.app["documents"]:
        doc = ev.docs.get(d["docId"])
        if not doc:
            continue
        scanned += 1
        for hit in guard.scan(doc["content"]):
            evidence.append(f"{d['documentType']} {d['docId']}: {hit['rule']} -- \"{hit['text']}\"")
    if not scanned:
        return result("prompt_injection", label, "skipped", "No document could be read to scan.", [])
    if evidence:
        return result("prompt_injection", label, "fail",
                      f"{len(evidence)} passage(s) in the filed documents read like instructions to an automated "
                      "reviewer. A genuine deed has none; the file needs a human to look at it.", evidence)
    return result("prompt_injection", label, "pass", f"{scanned} document(s) scanned; no instruction-like text.",
                  [f"documents scanned: {scanned}"])


async def check_boundary(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    """The parcel has a surveyed boundary on the ledger that encloses the recorded area."""
    label = "The parcel has a surveyed boundary on the ledger"
    b = ev.parcel.get("boundary")
    if not b:
        return result("boundary", label, "warn", "The ledger holds no surveyed boundary for this parcel, so its "
                      "location and shape cannot be checked.", [f"recorded area: {ev.parcel['areaSqm']} sq.m"])
    area, recorded = ev.parcel.get("boundaryAreaSqm"), ev.parcel["areaSqm"]
    dev = abs(area - recorded) / recorded * 100 if area and recorded else 0.0
    evidence = [f"boundary: {area} sq.m in {ev.parcel.get('corners')} corners", f"recorded area: {recorded} sq.m",
                f"centroid: {ev.parcel['centroid'][1]:.5f} N, {ev.parcel['centroid'][0]:.5f} E"]
    if dev > settings.EXTENT_WARN_PCT:
        return result("boundary", label, "warn", f"The boundary encloses {area} sq.m but {recorded} sq.m is recorded "
                      f"({dev:.1f}% apart).", evidence)
    return result("boundary", label, "pass", f"The boundary encloses {area} sq.m against {recorded} sq.m recorded.",
                  evidence)


async def check_boundary_overlap(ev: Evidence, tools: Tools, trace: Trace) -> dict[str, Any]:
    """No other registered parcel claims the same ground (the geospatial database, not the ledger, can answer this)."""
    label = "No other parcel's boundary overlaps this one"
    if not ev.parcel.get("boundary"):
        return result("boundary_overlap", label, "skipped", "There is no boundary to compare.", [])
    try:
        res = await tools.geo_overlaps(ev.parcel["surveyNo"], "Two registered parcels must never claim the same ground")
    except Exception as exc:
        return result("boundary_overlap", label, "skipped", f"The geospatial database could not be read ({str(exc)[:80]}).", [])
    hits = [(f["id"], f["properties"]["overlapSqm"], f["properties"].get("overlapPctOfThis")) for f in res["features"]]
    hits = [h for h in hits if h[1] >= 1.0]
    if hits:
        return result("boundary_overlap", label, "fail",
                      f"{len(hits)} registered parcel(s) overlap this parcel's boundary: the same ground is recorded twice.",
                      [f"{sy} overlaps by {sqm} sq.m ({pct}% of this parcel)" for sy, sqm, pct in hits])
    return result("boundary_overlap", label, "pass", "No registered boundary overlaps this parcel.",
                  ["overlapping parcels: 0"])


CHECKS = [check_document_integrity, check_document_instructions, check_anchor_uniqueness, check_required_documents, check_deed_parcel,
          check_party_names, check_consideration, check_documents_from_seller, check_encumbrance,
          check_consent_signatures, check_resale_velocity, check_prior_flags, check_valuation,
          check_boundary, check_boundary_overlap]


def verdict_of(checks: list[dict[str, Any]]) -> dict[str, Any]:
    fails = sum(1 for c in checks if c["status"] == "fail")
    warns = sum(1 for c in checks if c["status"] == "warn")
    judged = [c for c in checks if c["status"] in ("pass", "warn", "fail")]
    status = "flagged" if fails else "attention" if warns else "clear"
    return {"status": status, "flagCount": fails + warns, "failed": fails, "attention": warns,
            "passed": sum(1 for c in checks if c["status"] == "pass"),
            "skipped": sum(1 for c in checks if c["status"] == "skipped"),
            "confidence": round(len(judged) / len(checks), 2) if checks else 0.0,
            "rule": "flagged if any check fails; attention if any check warns; clear otherwise"}
