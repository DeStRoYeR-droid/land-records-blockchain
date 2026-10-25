"""Synthetic validator output for the agent tests: no ledger, no model."""

from __future__ import annotations

from typing import Any

from agent import facts as facts_mod
from agent.checks import verdict_of
from agent.explainer import template_explanation
from agent.pgp import SigningKey
from agent.report import assemble
from nodes.common.canonical import canonical

APP = {"appId": "APP-TEST-001", "surveyNo": "SY-TEST-12/4A", "sellerName": "Sunitha Reddy", "buyerName": "Arjun Rao",
       "sellerRef": "aa" * 32, "buyerRef": "bb" * 32, "considerationINR": 3850000, "status": "WITH_REVENUE",
       "validations": []}
PARCEL = {"village": "Kazipet", "district": "Hanamkonda", "areaSqm": 1450}


def check(cid: str, label: str, status: str, detail: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return {"id": cid, "label": label, "status": status, "detail": detail, "evidence": evidence or []}


PASSING = [check("anchor_uniqueness", "Each document is anchored only to this application", "pass", "All 2 fingerprints are unique."),
           check("required_documents", "Sale deed and encumbrance certificate are on file", "pass", "Both documents are present."),
           check("consent_signatures", "Both parties' consents are DID-signed", "pass", "Both signatures verified.")]
EXTENT_FAIL = check("deed_parcel", "The sale deed describes this parcel", "fail",
                    "The deed's extent (2320.0 sq.m) differs from the recorded 1450 sq.m by 60.0%.",
                    ["deed extent: 2320.0 sq.m", "recorded area: 1450 sq.m"])
PRICE_WARN = check("valuation", "Price is in line with comparable transfers in the district", "warn",
                   "Rs. 2655 per sq.m is 40% of the district median of Rs. 6640 per sq.m: possible under-valuation.",
                   ["district median: Rs. 6640 per sq.m (ratio 0.40)"])
SKIPPED = check("encumbrance", "The encumbrance certificate shows no charges", "skipped", "No certificate to read.")


def scenario(kind: str) -> dict[str, Any]:
    checks = {"clear": PASSING, "attention": PASSING + [PRICE_WARN, SKIPPED],
              "flagged": PASSING + [EXTENT_FAIL, PRICE_WARN]}[kind]
    verdict = verdict_of(checks)
    return {"checks": checks, "verdict": verdict, "facts": facts_mod.build_facts(APP, PARCEL, checks, verdict)}


def drafts_for(facts: dict[str, Any], texts: dict[str, str] | None = None) -> dict[str, Any]:
    out = {}
    for aud in ("officer", "citizen"):
        text = (texts or {}).get(aud) or template_explanation(facts, aud)
        out[aud] = {"audience": aud, "text": text, "writer": "template", "model": None, "fallbackReason": "test",
                    "gen": None, "drafts": 0,
                    "attempts": [{"attempt": 1, "writer": "template", "passed": True, "faithfulness": 1.0,
                                  "hallucinationRate": 0.0, "violations": [], "text": text, "gen": None}]}
    return out


def signed_report(kind: str = "flagged", texts: dict[str, str] | None = None, key: SigningKey | None = None,
                  tamper=None) -> dict[str, Any]:
    """A complete, correctly signed v2 report and its certificate. `tamper(report)` may alter the report
    BEFORE it is serialised and signed (a dishonest writer, not an altered file)."""
    from agent import quality
    s = scenario(kind)
    drafts = drafts_for(s["facts"], texts)
    measures = {a: quality.measure(d["text"], s["facts"], a) for a, d in drafts.items()}
    key = key or SigningKey.generate("Test Validator <test@example.org>")
    report, cert = assemble(app=APP, parcel=PARCEL, verdict=s["verdict"], checks=s["checks"], facts=s["facts"],
                            drafts=drafts, measures=measures, judges={}, trigger={"kind": "test"}, key_did="did:key:test",
                            key_fingerprint=key.fingerprint, trace_cid="bafkreitest", model_id=None,
                            graph={"name": "t", "version": "0", "nodes": []}, libraries={}, prompt_changed=False,
                            notice="This report does not approve or reject anything.")
    if tamper:
        tamper(report)
    rb = canonical(report)
    return {"report": report, "report_bytes": rb, "signature": key.sign_detached(rb), "cert": cert,
            "cert_signature": key.sign_detached(cert), "public_key": key.public_key_armored(), "key": key}
