"""
The independent verifier.

It re-checks a finished report using only the report's own bytes, the
signature, the public key and (optionally) the trace and the certificate. It
imports none of the code that *wrote* the report: not the graph, not the
model, not the ledger client. What it shares with the writer is only the
definitions of the rules (`verdict_of`, the measurer) and the file formats,
which is what lets anybody reproduce the verdict and the scores.

Use it in two places:

  * inside the graph, after signing and before anchoring. A report that fails
    here is never anchored (the run ends "quarantined").
  * on your own machine, long after the fact:

        python -m agent.verifier report.json report.json.asc validator.asc \\
            [--trace trace.json] [--certificate certificate.txt --certificate-sig certificate.txt.asc]

Each finding has an id, a plain-language label, a result and a detail.
Exit status 0 means every finding passed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from dataclasses import dataclass
from typing import Any

from nodes.common.canonical import canonical, sha256_hex

from . import certificate, guard, quality
from .grounding import ground
from .checks import verdict_of
from .cidstore import digest_of
from .facts import facts_from_report, facts_sha256
from .pgp import read_public_key, verify_detached

SCHEMAS = ("landchain.validation-report/v1", "landchain.validation-report/v2", "landchain.validation-report/v3")
READ_ONLY_TOOLS = {"nearby_sale_prices", "party_other_files", "parcel_ownership_chain", "parcel_neighbours",
                   "parcel_boundary_overlaps", "parcel_files", "read_document"}
FORBIDDEN_KEYS = {"decision", "approved", "rejected", "approval", "recommendation", "outcome"}
REQUIRED = {"schema": str, "reportId": str, "applicationId": str, "surveyNo": str, "parties": dict,
            "considerationINR": int, "validator": dict, "generatedAt": str, "verdict": dict, "checks": list,
            "explanation": dict, "reasoningTraceCID": str, "notice": str}
REQUIRED_V2 = {"parcel": dict, "policy": dict, "quality": dict, "provenance": dict, "certificate": dict}
SIGNATURE_SKEW_S = 900


@dataclass
class Finding:
    id: str
    label: str
    ok: bool | None            # None: not applicable / not supplied
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "label": self.label, "result": "pass" if self.ok else "skip" if self.ok is None else "fail",
                "detail": self.detail}


def _when(ts: str) -> dt.datetime | None:
    try:
        v = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)
    except (ValueError, AttributeError):
        return None


def verify_report(report_bytes: bytes, signature: str, public_key: str, *, trace_bytes: bytes | None = None,
                  certificate_bytes: bytes | None = None, certificate_signature: str | None = None,
                  report_cid: str | None = None, trace_cid: str | None = None) -> dict[str, Any]:
    f: list[Finding] = []
    sha = sha256_hex(report_bytes)

    # 1. it is JSON and has the shape we expect
    try:
        report = json.loads(report_bytes.decode("utf-8"))
        assert isinstance(report, dict)
    except (ValueError, AssertionError, UnicodeDecodeError) as exc:
        f.append(Finding("json", "The report is valid JSON", False, str(exc)))
        return _result(f, sha, None)
    f.append(Finding("json", "The report is valid JSON", True))
    v2 = report.get("schema") in SCHEMAS[1:]
    v3 = report.get("schema") == SCHEMAS[2]
    want = {**REQUIRED, **(REQUIRED_V2 if v2 else {}), **({"investigation": dict} if v3 else {})}
    problems = [f"{k} missing or not {t.__name__}" for k, t in want.items() if not isinstance(report.get(k), t)]
    if report.get("schema") not in SCHEMAS:
        problems.insert(0, f"unknown schema {report.get('schema')!r}")
    f.append(Finding("schema", "The report has every field its schema requires", not problems, "; ".join(problems[:4])))
    if problems:
        return _result(f, sha, report)

    # 2. the bytes are exactly what the writer would produce (no re-ordering, no whitespace games)
    f.append(Finding("canonical", "The bytes are canonical JSON (sorted keys, no spare whitespace)",
                     canonical(report) == report_bytes, "re-serialising gives different bytes"
                     if canonical(report) != report_bytes else ""))

    # 3. the content address
    if report_cid:
        try:
            ok = digest_of(report_cid) == hashlib.sha256(report_bytes).digest()
        except ValueError as exc:
            ok = False
            report_cid = f"{report_cid} ({exc})"
        f.append(Finding("cid", "The report's content identifier (CID) matches its bytes", ok, "" if ok else
                         f"{report_cid} does not name these bytes"))

    # 4. the signature, and that it was made by the key the report names
    verification = verify_detached(public_key, report_bytes, signature)
    named = report["validator"].get("pgpFingerprint")
    try:
        fingerprint = read_public_key(public_key).fingerprint
    except Exception:
        fingerprint = None
    f.append(Finding("signature", "The OpenPGP signature is valid for these exact bytes", verification.valid,
                     verification.reason))
    f.append(Finding("signer", "The signing key is the key the report names", bool(fingerprint) and fingerprint == named,
                     "" if fingerprint == named else f"key is {fingerprint}, report names {named}"))
    gen, created = _when(report["generatedAt"]), verification.created
    if gen and created:
        skew = abs(created - gen.timestamp())
        f.append(Finding("freshness", "The signature was made when the report says it was written",
                         skew <= SIGNATURE_SKEW_S, f"{int(skew)} s apart" if skew > SIGNATURE_SKEW_S else ""))

    # 5. the verdict follows from the checks, by the written rule
    expected = verdict_of(report["checks"])
    same = expected == report["verdict"]
    f.append(Finding("verdict", "The verdict is what the fixed rule gives for these checks", same,
                     "" if same else f"rule gives {expected['status']}, report says {report['verdict'].get('status')}"))

    # 6. the agent claims no authority
    bad_keys = sorted(FORBIDDEN_KEYS & set(report))
    f.append(Finding("authority", "The report decides nothing (evidence only)",
                     not bad_keys and "does not approve" in report["notice"].lower(),
                     f"contains decision fields: {bad_keys}" if bad_keys else ""))

    if v2:
        facts = facts_from_report(report)
        prov = report["provenance"]
        f.append(Finding("facts", "The facts the writer was given are the ones in the report",
                         facts_sha256(facts) == prov.get("factsSha256"), "facts hash differs"
                         if facts_sha256(facts) != prov.get("factsSha256") else ""))

        # 7. re-measure both explanations; the stored scores must be the ones we get
        hq = (report["quality"].get("hallucination") or {})
        for aud in ("officer", "citizen"):
            text = report["explanation"].get(aud, "")
            m = quality.measure(text, facts, aud, hq.get(aud, {}).get("threshold", 0.8))
            stored = hq.get(aud)
            same_scores = bool(stored) and stored.get("scores") == m["scores"] and stored.get("passed") == m["passed"]
            f.append(Finding(f"measure.{aud}", f"The {aud} explanation scores what the report says it scores",
                             same_scores, "" if same_scores else "recomputed scores differ from the stored ones"))
            f.append(Finding(f"faithful.{aud}", f"The {aud} explanation sticks to the evidence", m["passed"],
                             "; ".join(m["hardViolations"][:3]) or f"faithfulness {m['scores']['faithfulness']}"))
        hits = guard.scan(report["explanation"].get("officer", "") + "\n" + report["explanation"].get("citizen", ""))
        f.append(Finding("explanation.clean", "The explanations contain no instruction-like text", not hits,
                         hits[0]["text"] if hits else ""))

        # 8. the certificate is the report, rendered
        body = {k: v for k, v in report.items() if k != "certificate"}
        rendered = certificate.render(body).encode("utf-8")
        cert_ok = hashlib.sha256(rendered).hexdigest() == report["certificate"].get("sha256")
        f.append(Finding("certificate.hash", "The report's certificate hash is the hash of the rendered certificate",
                         cert_ok, "" if cert_ok else "re-rendering the certificate gives a different hash"))
        if certificate_bytes is not None:
            f.append(Finding("certificate.file", "The certificate file is the one the report describes",
                             certificate_bytes == rendered, "file differs from the rendering"
                             if certificate_bytes != rendered else ""))
        if certificate_bytes is not None and certificate_signature:
            cv = verify_detached(public_key, certificate_bytes, certificate_signature)
            f.append(Finding("certificate.signature", "The certificate's own signature is valid", cv.valid, cv.reason))
    else:
        f.append(Finding("facts", "Facts, quality scores and certificate (v2 reports only)", None, "this is a v1 report"))

    # 8b. the model's investigation: read-only, every accepted finding grounded, the check agrees with the findings
    if v3:
        inv = report["investigation"]
        chk = next((c for c in report["checks"] if c["id"] == "agent_investigation"), None)
        if not inv.get("ran"):
            f.append(Finding("investigation", "The AI investigation (did not run)", None, inv.get("reason") or ""))
        else:
            calls = {c["id"]: c for c in inv.get("calls", [])}
            ran = {c["name"] for c in calls.values() if c.get("ok")}       # refused requests never ran
            stray = sorted(ran - READ_ONLY_TOOLS)
            refused_calls = sum(1 for c in calls.values() if not c.get("ok"))
            f.append(Finding("investigation.readonly", "Every tool call that ran was a read-only tool", not stray,
                             f"unexpected tools ran: {stray}" if stray else
                             f"{len(calls) - refused_calls} ran, {refused_calls} refused before running"))
            facts_ = inv.get("briefFacts") or {}
            ungrounded = [x["claim"][:60] for x in inv.get("findings", []) if ground(x, calls, facts_)]
            f.append(Finding("investigation.grounded", "Every accepted finding is grounded in the tool results it cites",
                             not ungrounded, "; ".join(ungrounded[:2]) if ungrounded else
                             f"{len(inv.get('findings', []))} accepted, {len(inv.get('refused', []))} refused"))
            wrongly = [x["claim"][:60] for x in inv.get("refused", []) if not ground(x, calls, facts_)]
            f.append(Finding("investigation.refused", "Every refused finding really fails the grounding check", not wrongly,
                             "; ".join(wrongly[:2])))
            concerns = [x for x in inv.get("findings", []) if x.get("severity") == "concern"]
            ok = chk is not None and (chk["status"] == "warn") == bool(concerns) and chk["status"] != "fail"
            f.append(Finding("investigation.check", "The investigation's check says what its findings say, and never fails a file",
                             ok, "" if ok else f"check is {chk and chk['status']}, accepted concerns: {len(concerns)}"))

    # 9. the reasoning trace agrees with the report
    if trace_bytes is not None:
        tc = trace_cid or report["reasoningTraceCID"]
        try:
            addr = digest_of(tc) == hashlib.sha256(trace_bytes).digest()
        except ValueError:
            addr = False
        f.append(Finding("trace.cid", "The trace is the one the report points to", addr,
                         "" if addr else "trace bytes do not match the CID in the report"))
        try:
            trace = json.loads(trace_bytes)
            traced = {s["name"]: s["status"] for s in trace["steps"] if s.get("kind") == "check"}
            wrong = [c["id"] for c in report["checks"] if traced.get(c["id"]) != c["status"]]
            f.append(Finding("trace.checks", "Every check result in the report appears, unchanged, in the trace",
                             not wrong, f"differ or missing: {wrong}" if wrong else ""))
        except (ValueError, KeyError, TypeError) as exc:
            f.append(Finding("trace.checks", "Every check result in the report appears, unchanged, in the trace",
                             False, f"unreadable trace: {exc}"))
    return _result(f, sha, report)


def _result(findings: list[Finding], sha: str, report: dict[str, Any] | None) -> dict[str, Any]:
    failed = [x for x in findings if x.ok is False]
    return {"passed": not failed, "sha256": sha, "reportId": (report or {}).get("reportId"),
            "findings": [x.as_dict() for x in findings],
            "summary": f"{sum(1 for x in findings if x.ok)} passed, {len(failed)} failed, "
                       f"{sum(1 for x in findings if x.ok is None)} not applicable"}


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent.verifier", description=__doc__.split("\n\n")[0])
    ap.add_argument("report")
    ap.add_argument("signature")
    ap.add_argument("public_key")
    ap.add_argument("--trace")
    ap.add_argument("--certificate")
    ap.add_argument("--certificate-sig")
    ap.add_argument("--json", action="store_true", help="print the findings as JSON")
    a = ap.parse_args(argv)
    read = lambda p: open(p, "rb").read()  # noqa: E731
    res = verify_report(read(a.report), read(a.signature).decode(), read(a.public_key).decode(),
                        trace_bytes=read(a.trace) if a.trace else None,
                        certificate_bytes=read(a.certificate) if a.certificate else None,
                        certificate_signature=read(a.certificate_sig).decode() if a.certificate_sig else None)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"report {res['reportId']}  sha256 {res['sha256']}")
        for x in res["findings"]:
            mark = {"pass": "  ok  ", "fail": " FAIL ", "skip": " skip "}[x["result"]]
            print(f"[{mark}] {x['label']}" + (f"  -- {x['detail']}" if x["detail"] and x["result"] != "pass" else ""))
        print(("VERIFIED: " if res["passed"] else "NOT VERIFIED: ") + res["summary"])
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
