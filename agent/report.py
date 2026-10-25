"""
Assembling the report: pure data in, (report dict, certificate bytes) out.

Kept apart from the graph so that tests, and anyone modifying the report's
shape, can build a report without a ledger or a model. The independent
verifier (verifier.py) is the other half of this contract: whatever is put in
here, it re-derives or re-measures.
"""

from __future__ import annotations

import hashlib
from typing import Any

from nodes.common import crypto

from . import certificate, facts as facts_mod, prompts, quality, settings

AUDIENCES = ("officer", "citizen")
SCHEMA = "landchain.validation-report/v3"


def _generation(d: dict[str, Any]) -> dict[str, Any]:
    g = (d.get("gen") or next((a["gen"] for a in reversed(d["attempts"]) if a.get("gen")), None)) or {}
    return {"writer": d["writer"], "model": d.get("model"), "drafts": d["drafts"],
            "fallbackReason": d.get("fallbackReason"),
            "attempts": [{k: a[k] for k in ("attempt", "writer", "passed", "faithfulness", "violations")}
                         for a in d["attempts"]],
            "tokensPerSecond": g.get("tokensPerSecond"), "completionTokens": g.get("completionTokens"),
            "latencyMs": g.get("ms")}


def assemble(*, app: dict[str, Any], parcel: dict[str, Any], verdict: dict[str, Any], checks: list[dict[str, Any]],
             facts: dict[str, Any], drafts: dict[str, Any], measures: dict[str, Any], judges: dict[str, Any],
             trigger: dict[str, Any], key_did: str, key_fingerprint: str, trace_cid: str, model_id: str | None,
             graph: dict[str, Any], libraries: dict[str, str], prompt_changed: bool, notice: str,
             investigation: dict[str, Any] | None = None, reflections: dict[str, Any] | None = None,
             generated_at: str | None = None) -> tuple[dict[str, Any], bytes]:
    scores = [measures[a]["scores"] for a in AUDIENCES]
    injection_hits = next((len(c["evidence"]) for c in checks if c["id"] == "prompt_injection" and c["status"] == "fail"), 0)
    previous = len(app.get("validations") or [])
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "reportId": f"VR-{app['appId']}-{previous + 1}",
        "applicationId": app["appId"], "surveyNo": app["surveyNo"],
        "parcel": facts_mod.parcel_summary(parcel),
        "parties": {"seller": {"name": app["sellerName"], "ownerRef": app["sellerRef"]},
                    "buyer": {"name": app["buyerName"], "ownerRef": app["buyerRef"]}},
        "considerationINR": app["considerationINR"],
        "trigger": trigger, "applicationStatusAtReview": app["status"],
        "validator": {"name": settings.AGENT_USER, "mspId": settings.AGENT_MSP, "role": "agent", "did": key_did,
                      "pgpFingerprint": key_fingerprint, "software": settings.SOFTWARE},
        "generatedAt": generated_at or crypto.iso(crypto.utcnow()),
        "verdict": verdict, "checks": checks,
        "investigation": investigation or {"ran": False},
        "policy": {"thresholds": facts_mod.thresholds(), "faithfulnessMin": settings.FAITHFULNESS_MIN,
                   "rule": verdict["rule"]},
        "explanation": {a: drafts[a]["text"] for a in AUDIENCES}
        | {f"{a}Writer": {"engine": drafts[a]["writer"], "model": drafts[a].get("model")} for a in AUDIENCES},
        "quality": {
            "hallucination": {a: quality.summarise(measures[a]) for a in AUDIENCES},
            "generation": {a: _generation(d) for a, d in drafts.items()},
            "judge": {a: judges.get(a) for a in AUDIENCES},
            "reflections": {a: (reflections or {}).get(a, []) for a in AUDIENCES},
            "promptInjection": {"hits": injection_hits, "promptSanitised": prompt_changed},
            "aggregate": {
                "minFaithfulness": min(s["faithfulness"] for s in scores),
                "maxHallucinationRate": max(s["hallucinationRate"] for s in scores),
                "retries": sum(max(0, d["drafts"] - 1) for d in drafts.values()),
                "fallbacks": sum(1 for d in drafts.values() if d["writer"] == "template" and d.get("model") is None
                                 and any(a["writer"] == "omlx" for a in d["attempts"])),
                "modelWritten": sum(1 for d in drafts.values() if d["writer"] == "omlx")}},
        "provenance": {"graph": graph, "libraries": libraries, "factsSha256": facts_mod.facts_sha256(facts),
                       "promptTemplateSha256": prompts.template_sha256(),
                       "model": {"id": model_id, "temperature": settings.OMLX_TEMPERATURE}},
        "reasoningTraceCID": trace_cid,
        "notice": notice,
    }
    cert = certificate.render(report).encode("utf-8")
    report["certificate"] = {"filename": "certificate.txt", "sha256": hashlib.sha256(cert).hexdigest(), "bytes": len(cert)}
    return report, cert
