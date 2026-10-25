"""Signed transfer records: the public copy hides people, the party copy does not, both verify (also with gpg)."""

import os
import shutil
import subprocess

import pytest

from agent.pgp import SigningKey
from gateway import records

from .test_geo import square


@pytest.fixture()
def key(tmp_path):
    pgp = SigningKey.generate("Test Records Office <r@example>")
    return records.RecordsKey(pgp, pgp.public_key_armored(comment="test"))


def ctx():
    trail = [{"at": "2026-10-14T04:00:00.000Z", "by": "clerk1", "org": "Org1MSP", "role": "clerk",
              "action": "SUBMITTED", "status": "SUBMITTED", "txId": "a" * 64},
             {"at": "2026-10-14T04:10:00.000Z", "by": "clerk1", "org": "Org1MSP", "role": "clerk",
              "action": "ANCHORED SALEDEED DOC-9", "status": "SUBMITTED", "txId": "9" * 64},
             {"at": "2026-10-15T04:00:00.000Z", "by": "collector1", "org": "Org3MSP", "role": "collector",
              "action": "APPROVED -- ownership transferred", "status": "APPROVED", "txId": "b" * 64}]
    app = {"appId": "APP-1", "surveyNo": "SY-1", "status": "APPROVED", "sellerName": "Asha Rao", "buyerName": "Ravi Teja",
           "sellerRef": "r1", "buyerRef": "r2", "considerationINR": 4500000, "trail": trail,
           "documents": [{"documentType": "SALEDEED", "issuer": "SRO Hanamkonda", "sha256": "c" * 64, "docId": "DOC-9"}],
           "consent": {"intentSha256": "d" * 64, "sellerDid": "did:key:z6MkSELLERSELLERSELLER", "buyerDid": "did:key:z6MkBUYERBUYER"},
           "decision": {"outcome": "APPROVED", "at": "2026-10-15T04:00:00.000Z", "by": "collector1", "remarks": "ok"},
           "validations": [{"verdict": "clear", "flagCount": 0, "sha256": "e" * 64, "txId": "f" * 64,
                            "anchoredAt": "2026-10-14T05:00:00.000Z"}]}
    from chaincode.landrec import geometry
    b = geometry.parse_boundary(square())
    parcel = {"surveyNo": "SY-1", "village": "Kazipet", "district": "Hanamkonda", "areaSqm": 2500, "ownerName": "Ravi Teja",
              "status": "ACTIVE", "boundary": b, **geometry.describe(b)}
    chain = {"owners": 2, "links": [
        {"seq": 1, "ownerName": "Asha Rao", "since": "2026-10-01T00:00:00Z", "until": "2026-10-15T04:00:00.000Z",
         "acquired": "registration", "sinceTxId": "1" * 64},
        {"seq": 2, "ownerName": "Ravi Teja", "since": "2026-10-15T04:00:00.000Z", "until": None, "acquired": "transfer",
         "appId": "APP-1", "considerationINR": 4500000, "sinceTxId": "b" * 64}]}
    return {"application": app, "parcel": parcel, "chain": chain, "height": 42, "blocks": {"b" * 64: 40},
            "keyOnLedger": {"pgpFingerprint": "X", "attestedBy": "collector1"}}


def test_public_copy_hides_people_and_amounts(key):
    rec = records.build("public", ctx(), key)
    text = rec["text"]
    assert text.startswith("-----BEGIN PGP SIGNED MESSAGE-----") and "PUBLIC COPY" in text
    assert "Asha Rao" not in text and "Ravi Teja" not in text and "A. R." in text and "R. T." in text
    assert "45,00,000" not in text and "4500000" not in text and "DOC-9" not in text and "clerk1" not in text
    v = records.verify(text, key.public_armored)
    assert v["valid"] and v["record"]["copy"] == "public" and v["applicationId"] == "APP-1"


def test_party_copy_is_personal_and_signed(key):
    me = {"name": "Asha Rao", "digilockerId": "DL-1", "did": "did:key:z6MkSELLERSELLERSELLER", "role": "seller"}
    rec = records.build("party", ctx(), key, recipient=me)
    text = rec["text"]
    assert "PARTY COPY" in text and "Issued to:" in text and "Asha Rao (seller)" in text
    assert "Rs. 45,00,000" in text and "DOC-9" in text and "SUBMITTED -- clerk1 (clerk, Org1MSP)" in text
    assert rec["filename"].endswith("seller-copy.asc") and rec["recordId"].endswith("-SELLER-42")
    assert records.verify(text, key.public_armored)["valid"]
    tampered = text.replace("Rs. 45,00,000", "Rs. 4,50,000")
    assert not records.verify(tampered, key.public_armored)["valid"]


@pytest.mark.skipif(not shutil.which("gpg"), reason="gpg not installed")
def test_gpg_verifies_a_record(key, tmp_path):
    rec = records.build("public", ctx(), key)
    (tmp_path / "k.asc").write_text(key.public_armored)
    (tmp_path / "r.asc").write_text(rec["text"])
    home = tmp_path / "g"
    home.mkdir(mode=0o700)
    env = {**os.environ, "GNUPGHOME": str(home)}
    subprocess.run(["gpg", "--batch", "--import", str(tmp_path / "k.asc")], env=env, capture_output=True, check=True)
    out = subprocess.run(["gpg", "--batch", "--verify", str(tmp_path / "r.asc")], env=env, capture_output=True, text=True)
    assert out.returncode == 0 and "Good signature" in out.stderr
