"""
Keys, signatures and X.509 certificates.

Every Fabric identity is an ECDSA key pair on curve P-256 plus an X.509
certificate that binds the public key to a name, signed by the organisation's
Certificate Authority. This module wraps the `cryptography` library so the rest
of the code reads in those terms.

Two Fabric details are reproduced on purpose:

* Low-S signatures. An ECDSA signature (r, s) has a twin (r, n - s) that
  verifies just as well. Fabric only accepts the "low-S" form so a signature
  cannot be rewritten into a different-but-valid one (malleability). sign()
  normalises to low-S; verify() rejects high-S.

* Attributes inside the certificate. Fabric CA writes identity attributes such
  as role=clerk into a custom X.509 extension, OID 1.2.3.4.5.6.7.8.1, whose
  value is the raw JSON {"attrs": {...}}. The chaincode reads the role from the
  certificate itself, so a caller cannot simply claim a role in the arguments.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Union

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.x509.oid import NameOID, ObjectIdentifier

from .canonical import b64d, b64e

# Order of the P-256 group. Signatures with s > n/2 are "high-S".
P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
P256_HALF_N = P256_N >> 1

# The extension OID Fabric CA uses for identity attributes.
FABRIC_ATTRS_OID = ObjectIdentifier("1.2.3.4.5.6.7.8.1")

# Node OU values Fabric uses to classify identities.
NODE_OUS = ("client", "peer", "admin", "orderer")

PrivateKey = ec.EllipticCurvePrivateKey
PublicKey = ec.EllipticCurvePublicKey
PemLike = Union[str, bytes]


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def generate_private_key() -> PrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def private_key_to_pem(key: PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def load_private_key(pem: PemLike) -> PrivateKey:
    data = pem.encode() if isinstance(pem, str) else pem
    key = serialization.load_pem_private_key(data, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValueError("expected an EC private key")
    return key


def public_key_to_pem(key: PublicKey) -> str:
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


# ---------------------------------------------------------------------------
# Signatures (ECDSA P-256 / SHA-256, DER, base64 on the wire)
# ---------------------------------------------------------------------------

def sign(key: PrivateKey, data: bytes) -> str:
    """Sign bytes; returns base64 DER with s normalised to low-S."""
    der = key.sign(data, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    if s > P256_HALF_N:
        s = P256_N - s
    return b64e(encode_dss_signature(r, s))


def verify(public_key: PublicKey, data: bytes, signature_b64: str) -> bool:
    """True only for a valid, low-S signature over exactly these bytes."""
    try:
        der = b64d(signature_b64)
        _, s = decode_dss_signature(der)
        if s > P256_HALF_N:
            return False
        public_key.verify(der, data, ec.ECDSA(hashes.SHA256()))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------

def load_cert(pem: PemLike) -> x509.Certificate:
    data = pem.encode() if isinstance(pem, str) else pem
    return x509.load_pem_x509_certificate(data)


def cert_to_pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def cert_common_name(cert: x509.Certificate) -> str:
    cns = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return str(cns[0].value) if cns else ""


def cert_ous(cert: x509.Certificate) -> list[str]:
    return [str(a.value) for a in cert.subject.get_attributes_for_oid(NameOID.ORGANIZATIONAL_UNIT_NAME)]


def cert_node_ou(cert: x509.Certificate) -> str | None:
    """The identity's capacity (client/peer/admin/orderer), from its OU."""
    for ou in cert_ous(cert):
        if ou in NODE_OUS:
            return ou
    return None


def cert_attributes(cert: x509.Certificate) -> dict[str, str]:
    """Attributes Fabric CA embedded in the certificate (role, dept, ...)."""
    try:
        ext = cert.extensions.get_extension_for_oid(FABRIC_ATTRS_OID)
    except x509.ExtensionNotFound:
        return {}
    raw = ext.value.value  # UnrecognizedExtension -> the raw JSON bytes
    try:
        return dict(json.loads(raw.decode("utf-8")).get("attrs", {}))
    except (ValueError, AttributeError):
        return {}


def _rdn_path(name: x509.Name) -> str:
    """X.500 name as /C=IN/O=.../OU=client/CN=clerk1 (most significant first)."""
    short = {
        NameOID.COUNTRY_NAME: "C",
        NameOID.STATE_OR_PROVINCE_NAME: "ST",
        NameOID.LOCALITY_NAME: "L",
        NameOID.ORGANIZATION_NAME: "O",
        NameOID.ORGANIZATIONAL_UNIT_NAME: "OU",
        NameOID.COMMON_NAME: "CN",
    }
    parts = []
    for rdn in name.rdns:
        for attr in rdn:
            parts.append(f"{short.get(attr.oid, attr.oid.dotted_string)}={attr.value}")
    return "/" + "/".join(parts)


def fabric_id_string(cert: x509.Certificate) -> str:
    """Same shape as Fabric's ClientIdentity.getID(): x509::<subject>::<issuer>."""
    return f"x509::{_rdn_path(cert.subject)}::{_rdn_path(cert.issuer)}"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_iso(ts: str) -> dt.datetime:
    """Parse an ISO-8601 timestamp (with Z or offset) into an aware datetime."""
    value = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value


def iso(value: dt.datetime) -> str:
    """UTC ISO-8601 with millisecond precision and a Z suffix."""
    return value.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
