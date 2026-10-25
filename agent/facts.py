"""
The facts: everything the language model may mention, and nothing else.

`build_facts` assembles them while a run is in progress; `facts_from_report`
rebuilds the *same* dictionary from a finished report, so the independent
verifier can re-measure the explanation without access to the live ledger.
The report records the SHA-256 of these facts, which is how the two are shown
to be identical.
"""

from __future__ import annotations

from typing import Any

from nodes.common.canonical import canonical, sha256_hex

from . import settings

CHECK_KEYS = ("id", "label", "status", "detail", "evidence")


def thresholds() -> dict[str, Any]:
    return {"extentWarnPct": settings.EXTENT_WARN_PCT, "extentFailPct": settings.EXTENT_FAIL_PCT,
            "rapidResaleDays": settings.RAPID_RESALE_DAYS}


def _assemble(application_id: str, survey_no: str, seller: str, buyer: str, consideration: int, parcel: dict[str, Any],
              checks: list[dict[str, Any]], verdict: dict[str, Any], limits: dict[str, Any]) -> dict[str, Any]:
    return {
        "applicationId": application_id, "surveyNo": survey_no, "sellerName": seller, "buyerName": buyer,
        "considerationINR": consideration,
        "village": parcel.get("village"), "district": parcel.get("district"), "areaSqm": parcel.get("areaSqm"),
        "verdict": {k: verdict[k] for k in ("status", "flagCount", "confidence")},
        "counts": {"pass": verdict["passed"], "warn": verdict["attention"], "fail": verdict["failed"],
                   "skipped": verdict["skipped"], "total": len(checks)},
        "thresholds": limits,
        "checks": [{k: c[k] for k in CHECK_KEYS} for c in checks],
    }


def build_facts(app: dict[str, Any], parcel: dict[str, Any], checks: list[dict[str, Any]],
                verdict: dict[str, Any]) -> dict[str, Any]:
    return _assemble(app["appId"], app["surveyNo"], app["sellerName"], app["buyerName"], app["considerationINR"],
                     parcel, checks, verdict, thresholds())


def parcel_summary(parcel: dict[str, Any]) -> dict[str, Any]:
    return {"village": parcel.get("village"), "district": parcel.get("district"), "areaSqm": parcel.get("areaSqm")}


def facts_from_report(report: dict[str, Any]) -> dict[str, Any]:
    parties = report["parties"]
    return _assemble(report["applicationId"], report["surveyNo"], parties["seller"]["name"], parties["buyer"]["name"],
                     report["considerationINR"], report.get("parcel") or {}, report["checks"], report["verdict"],
                     (report.get("policy") or {}).get("thresholds") or {})


def facts_sha256(facts: dict[str, Any]) -> str:
    return sha256_hex(canonical(facts))
