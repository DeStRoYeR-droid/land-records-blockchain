"""
did:key for Ed25519 -- a self-certifying decentralized identifier.

    did:key:z6Mk...  =  "did:key:" + "z" + base58btc( 0xed 0x01 || 32-byte public key )

The identifier IS the public key, so "resolving" it needs no registry, no
network and no clock: decode the string and you have the key. That makes it
safe to verify inside chaincode, which must be deterministic.

What a did:key does NOT prove is who the person is. That binding (this DID
belongs to the citizen behind ownerRef X) is made by the Registration
department after DigiLocker KYC, and recorded on the ledger by RegisterCitizen.
"""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}
ED25519_MULTICODEC = b"\xed\x01"


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n > 0:
        n, rem = divmod(n, 58)
        out = _ALPHABET[rem] + out
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + out


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        if ch not in _INDEX:
            raise ValueError(f"invalid base58 character {ch!r}")
        n = n * 58 + _INDEX[ch]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + body


def did_from_public_key(raw32: bytes) -> str:
    if len(raw32) != 32:
        raise ValueError("Ed25519 public keys are 32 bytes")
    return "did:key:z" + b58encode(ED25519_MULTICODEC + raw32)


def public_key_from_did(did: str) -> bytes:
    if not isinstance(did, str) or not did.startswith("did:key:z"):
        raise ValueError("expected a did:key identifier starting with 'did:key:z'")
    decoded = b58decode(did[len("did:key:z"):])
    if decoded[:2] != ED25519_MULTICODEC or len(decoded) != 34:
        raise ValueError("did:key is not an Ed25519 key")
    return decoded[2:]


def verify(did: str, message: bytes, signature: bytes) -> bool:
    """True if `signature` is the DID holder's Ed25519 signature over `message`."""
    try:
        Ed25519PublicKey.from_public_bytes(public_key_from_did(did)).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False
