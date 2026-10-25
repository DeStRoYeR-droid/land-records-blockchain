"""OpenPGP interop: our signatures must verify with GnuPG, and tampering must fail."""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from agent.cidstore import cid_v1_raw, digest_of
from agent.pgp import SigningKey, verify_detached

DOC = json.dumps({"reportId": "VR-1", "verdict": {"status": "clear"}, "narrative": "Ünïcödé — ₹45,00,000"},
                 sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


@pytest.fixture(scope="module")
def key():
    return SigningKey.generate("LandChain Validator Agent (test) <validator@landchain.example>")


def test_python_roundtrip(key):
    sig = key.sign_detached(DOC)
    assert verify_detached(key.public_key_armored(), DOC, sig).valid
    bad = verify_detached(key.public_key_armored(), DOC.replace(b"clear", b"flagg"), sig)
    assert not bad.valid and "BAD" in bad.reason
    other = SigningKey.generate("someone else <x@y>")
    assert not verify_detached(other.public_key_armored(), DOC, sig).valid


def test_armor_checksum_detects_edits(key):
    sig = key.sign_detached(DOC)
    lines = sig.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln and not ln.startswith(("-", "Comment", "=")))
    lines[i] = ("B" if lines[i][0] != "B" else "C") + lines[i][1:]
    assert not verify_detached(key.public_key_armored(), DOC, "\n".join(lines)).valid


@pytest.mark.skipif(shutil.which("gpg") is None, reason="GnuPG not installed")
def test_gnupg_accepts_key_and_signature(key):
    home = Path(tempfile.mkdtemp())
    os.chmod(home, 0o700)
    env = {**os.environ, "GNUPGHOME": str(home)}
    (home / "pub.asc").write_text(key.public_key_armored())
    (home / "doc").write_bytes(DOC)
    (home / "doc.asc").write_text(key.sign_detached(DOC))
    (home / "bad").write_bytes(DOC + b"\n")
    subprocess.run(["gpg", "--batch", "--import", str(home / "pub.asc")], env=env, check=True, capture_output=True)
    good = subprocess.run(["gpg", "--batch", "--verify", str(home / "doc.asc"), str(home / "doc")],
                          env=env, capture_output=True, text=True)
    assert good.returncode == 0 and "Good signature" in good.stderr
    assert key.fingerprint in good.stderr.replace(" ", "")
    bad = subprocess.run(["gpg", "--batch", "--verify", str(home / "doc.asc"), str(home / "bad")],
                         env=env, capture_output=True, text=True)
    assert bad.returncode != 0 and "BAD signature" in bad.stderr


def test_cid_is_ipfs_raw_cidv1():
    cid = cid_v1_raw(b"hello")
    assert cid.startswith("bafkrei") and len(digest_of(cid)) == 32
    # `ipfs add --cid-version=1 --raw-leaves` of "hello" (no newline) gives this CID
    assert cid == "bafkreibm6jg3ux5qumhcn2b3flc3tyu6dmlb4xa7u5bf44yegnrjhc4yeq"
