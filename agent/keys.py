"""
The validator's signing key: one Ed25519 key, two encodings.

  OpenPGP   for portable document signatures (gpg / OpenPGP.js verify them)
  did:key   for the agent's decentralized identity -- the same standard the
            citizens use, so humans and the agent speak one identity language

Stored in data/agent/keys/ (private key 0600). Generated on first start;
a Revenue officer then attests the fingerprint on the ledger
(./landchain.sh attest-agent, done automatically by ./initial_setup.sh).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from chaincode.landrec import didkey

from . import settings
from .pgp import SigningKey, format_fingerprint

KEY_DIR = settings.DATA_DIR / "keys"


@dataclass
class AgentKey:
    pgp: SigningKey
    did: str
    public_armored: str

    @property
    def fingerprint(self) -> str:
        return self.pgp.fingerprint

    def describe(self) -> dict:
        return {"pgpFingerprint": self.pgp.fingerprint, "pgpFingerprintFormatted": format_fingerprint(self.pgp.fingerprint),
                "keyId": self.pgp.key_id, "userId": self.pgp.user_id, "created": self.pgp.created,
                "did": self.did, "publicKey": self.public_armored, "algorithm": "EdDSA (Ed25519), OpenPGP v4"}


def load_or_create(agent_name: str) -> AgentKey:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    key_file, meta_file = KEY_DIR / "validator.key", KEY_DIR / "validator.json"
    if key_file.exists() and meta_file.exists():
        private = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
        assert isinstance(private, Ed25519PrivateKey)
        meta = json.loads(meta_file.read_text())
        pgp = SigningKey(private, meta["userId"], meta["created"])
    else:
        user_id = f"LandChain Validator Agent ({agent_name}, Org2 Revenue) <validator@org2.landchain.example>"
        pgp = SigningKey.generate(user_id)
        key_file.write_bytes(pgp.private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
        os.chmod(key_file, 0o600)
        meta_file.write_text(json.dumps({"userId": pgp.user_id, "created": pgp.created}))
    armored = pgp.public_key_armored()
    (KEY_DIR / "validator.pub.asc").write_text(armored)
    return AgentKey(pgp, didkey.did_from_public_key(pgp.public_raw), armored)
