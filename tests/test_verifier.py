"""The independent verifier must notice every way a report can be wrong."""

import copy
import json

import pytest

from agent import certificate, verifier
from agent.pgp import SigningKey
from nodes.common.canonical import canonical

from tests.agent_fixtures import signed_report


def run(sr, **over):
    kw = dict(certificate_bytes=sr["cert"], certificate_signature=sr["cert_signature"])
    kw.update(over)
    return verifier.verify_report(over.pop("report_bytes", sr["report_bytes"]), over.pop("signature", sr["signature"]),
                                  over.pop("public_key", sr["public_key"]), **{k: v for k, v in kw.items()
                                                                              if k not in ("report_bytes", "signature", "public_key")})


def failed(res):
    return {f["id"] for f in res["findings"] if f["result"] == "fail"}


@pytest.mark.parametrize("kind", ["clear", "attention", "flagged"])
def test_an_honest_report_verifies(kind):
    sr = signed_report(kind)
    res = run(sr)
    assert res["passed"], failed(res)
    assert {f["id"] for f in res["findings"]} >= {"json", "schema", "canonical", "signature", "signer", "verdict", "authority",
                                                   "facts", "measure.officer", "faithful.citizen", "certificate.hash",
                                                   "certificate.signature"}


def test_a_changed_byte_breaks_the_signature():
    sr = signed_report()
    bad = sr["report_bytes"].replace(b"Sunitha", b"Sunithe", 1)
    assert "signature" in failed(verifier.verify_report(bad, sr["signature"], sr["public_key"]))


def test_pretty_printing_breaks_canonical_form_and_signature():
    sr = signed_report()
    pretty = json.dumps(json.loads(sr["report_bytes"]), indent=2).encode()
    assert {"canonical", "signature"} <= failed(verifier.verify_report(pretty, sr["signature"], sr["public_key"]))


def test_a_different_key_is_refused():
    sr = signed_report()
    other = SigningKey.generate("Someone Else <x@example.org>")
    assert {"signature", "signer"} & failed(verifier.verify_report(
        sr["report_bytes"], sr["signature"], other.public_key_armored()))


def test_a_report_signed_by_another_key_but_naming_ours():
    mallory = SigningKey.generate("Mallory <m@example.org>")
    honest = SigningKey.generate("Validator <v@example.org>")
    sr = signed_report(key=honest)
    forged_sig = mallory.sign_detached(sr["report_bytes"])
    assert "signature" in failed(verifier.verify_report(sr["report_bytes"], forged_sig, honest.public_key_armored()))


def resign(sr, mutate):
    """The writer lies AND signs: the signature is good, so only re-derivation can catch it."""
    rep = copy.deepcopy(sr["report"])
    mutate(rep)
    rb = canonical(rep)
    return rep, rb, sr["key"].sign_detached(rb)


def test_a_wrong_verdict_is_caught_even_when_signed():
    sr = signed_report("flagged")
    _, rb, sig = resign(sr, lambda r: r["verdict"].update(status="clear"))
    res = verifier.verify_report(rb, sig, sr["public_key"])
    assert "verdict" in failed(res) and "signature" not in failed(res)


def test_a_checked_status_edited_after_the_fact_is_caught():
    sr = signed_report("flagged")
    def flip(r):
        for c in r["checks"]:
            if c["status"] == "fail":
                c["status"] = "pass"
    _, rb, sig = resign(sr, flip)
    assert "verdict" in failed(verifier.verify_report(rb, sig, sr["public_key"]))


def test_a_hallucinated_explanation_is_caught_even_when_signed():
    sr = signed_report("flagged")
    _, rb, sig = resign(sr, lambda r: r["explanation"].update(officer=r["explanation"]["officer"] + " Value Rs. 99,99,999."))
    res = verifier.verify_report(rb, sig, sr["public_key"])
    assert {"faithful.officer", "measure.officer"} & failed(res)


def test_a_decision_in_the_report_is_caught():
    sr = signed_report()
    _, rb, sig = resign(sr, lambda r: r.update(decision="approved"))
    assert "authority" in failed(verifier.verify_report(rb, sig, sr["public_key"]))


def test_doctored_quality_scores_are_caught():
    sr = signed_report("flagged")
    _, rb, sig = resign(sr, lambda r: r["quality"]["hallucination"]["officer"]["scores"].update(faithfulness=0.5))
    assert "measure.officer" in failed(verifier.verify_report(rb, sig, sr["public_key"]))


def test_altered_facts_are_caught():
    sr = signed_report("flagged")
    _, rb, sig = resign(sr, lambda r: r.update(considerationINR=1))
    assert "facts" in failed(verifier.verify_report(rb, sig, sr["public_key"]))


def test_certificate_must_be_the_rendering_of_the_report():
    sr = signed_report("flagged")
    edited = sr["cert"].replace(b"FLAGGED", b"CLEAR", 1)
    res = run(sr, certificate_bytes=edited, certificate_signature=sr["key"].sign_detached(edited))
    assert "certificate.file" in failed(res)
    res = run(sr, certificate_bytes=edited)           # old signature no longer matches either
    assert {"certificate.file"} <= failed(res)


def test_trace_must_agree_with_the_report():
    sr = signed_report("flagged")
    steps = [{"kind": "check", "name": c["id"], "status": c["status"]} for c in sr["report"]["checks"]]
    trace = canonical({"schema": "t", "steps": steps})
    # the fixture's trace CID is a placeholder, so only the content cross-check is meaningful here
    assert "trace.checks" not in failed(run(sr, trace_bytes=trace))
    steps[0]["status"] = "fail" if steps[0]["status"] != "fail" else "pass"
    assert "trace.checks" in failed(run(sr, trace_bytes=canonical({"schema": "t", "steps": steps})))


def test_cli_exit_codes(tmp_path):
    sr = signed_report()
    for name, data in (("r.json", sr["report_bytes"]), ("r.asc", sr["signature"].encode()), ("k.asc", sr["public_key"].encode()),
                       ("c.txt", sr["cert"]), ("c.asc", sr["cert_signature"].encode())):
        (tmp_path / name).write_bytes(data)
    args = [str(tmp_path / "r.json"), str(tmp_path / "r.asc"), str(tmp_path / "k.asc"),
            "--certificate", str(tmp_path / "c.txt"), "--certificate-sig", str(tmp_path / "c.asc")]
    assert verifier.main(args) == 0
    (tmp_path / "r.json").write_bytes(sr["report_bytes"].replace(b"Kazipet", b"Kazipat", 1))
    assert verifier.main(args) == 1


def test_certificate_is_a_pure_function_of_the_report():
    sr = signed_report("flagged")
    body = {k: v for k, v in sr["report"].items() if k != "certificate"}
    assert certificate.render(body).encode() == sr["cert"] == certificate.render(body).encode()
