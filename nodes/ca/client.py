"""
CA client: what fabric-ca-client does.

enroll() generates the key pair HERE, sends only a CSR, and writes the result
as a Fabric-style MSP folder:

    msp/
      signcerts/cert.pem     the issued certificate
      keystore/key.pem       the private key (0600, never transmitted)
      cacerts/ca.pem         the CA's root certificate
      config.yaml            Node OU classification (which OU means client/peer/...)
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from ..common import crypto
from ..common.canonical import canonical
from ..common.msp import SigningIdentity
from ..common.web import error_message

NODE_OU_CONFIG = """NodeOUs:
  Enable: true
  ClientOUIdentifier:
    Certificate: cacerts/ca.pem
    OrganizationalUnitIdentifier: client
  PeerOUIdentifier:
    Certificate: cacerts/ca.pem
    OrganizationalUnitIdentifier: peer
  AdminOUIdentifier:
    Certificate: cacerts/ca.pem
    OrganizationalUnitIdentifier: admin
  OrdererOUIdentifier:
    Certificate: cacerts/ca.pem
    OrganizationalUnitIdentifier: orderer
"""


class CAClientError(Exception):
    pass


def cainfo(ca_url: str) -> dict[str, Any]:
    resp = httpx.get(f"{ca_url}/api/v1/cainfo", timeout=10)
    if resp.status_code != 200:
        raise CAClientError(error_message(resp))
    return resp.json()


def write_org_msp(msp_dir: Path, ca_cert_pem: str) -> None:
    """The org-level MSP: just trust roots + OU config, no private keys."""
    (msp_dir / "cacerts").mkdir(parents=True, exist_ok=True)
    (msp_dir / "cacerts" / "ca.pem").write_text(ca_cert_pem)
    (msp_dir / "config.yaml").write_text(NODE_OU_CONFIG)


def enroll(ca_url: str, name: str, secret: str, msp_dir: Path, msp_id: str,
           hosts: list[str] | None = None) -> SigningIdentity:
    key = crypto.generate_private_key()          # generated locally...
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
    if hosts:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]), critical=False)
    csr = builder.sign(key, hashes.SHA256())     # ...and only its public half is sent
    csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode()

    auth = base64.b64encode(f"{name}:{secret}".encode()).decode()
    resp = httpx.post(f"{ca_url}/api/v1/enroll", json={"csr": csr_pem},
                      headers={"Authorization": f"Basic {auth}"}, timeout=10)
    if resp.status_code != 200:
        raise CAClientError(f"enroll {name}: {error_message(resp)}")
    body = resp.json()

    msp_dir = Path(msp_dir)
    for sub in ("signcerts", "keystore", "cacerts"):
        (msp_dir / sub).mkdir(parents=True, exist_ok=True)
    for old in (msp_dir / "keystore").glob("*"):
        old.unlink()
    (msp_dir / "signcerts" / "cert.pem").write_text(body["cert"])
    key_file = msp_dir / "keystore" / "key.pem"
    key_file.write_bytes(crypto.private_key_to_pem(key))
    os.chmod(key_file, 0o600)
    (msp_dir / "cacerts" / "ca.pem").write_text(body["caCert"])
    (msp_dir / "config.yaml").write_text(NODE_OU_CONFIG)
    return SigningIdentity(msp_id, body["cert"], key)


def _signed_headers(registrar: SigningIdentity, path: str, body: dict[str, Any]) -> dict[str, str]:
    return {
        "X-Registrar-Cert": base64.b64encode(registrar.cert_pem.encode()).decode(),
        "X-Registrar-Signature": registrar.sign(canonical({"path": path, "body": body})),
    }


def register(ca_url: str, registrar: SigningIdentity, name: str, secret: str, id_type: str,
             attrs: dict[str, str] | None = None) -> bool:
    """Register an identity. Returns False if it already existed (safe to re-run)."""
    body = {"name": name, "secret": secret, "type": id_type, "attrs": attrs or {}}
    resp = httpx.post(f"{ca_url}/api/v1/register", json=body,
                      headers=_signed_headers(registrar, "/api/v1/register", body), timeout=10)
    if resp.status_code == 409:
        return False
    if resp.status_code != 200:
        raise CAClientError(f"register {name}: {error_message(resp)}")
    return True


def list_identities(ca_url: str, registrar: SigningIdentity) -> list[dict[str, Any]]:
    resp = httpx.get(f"{ca_url}/api/v1/identities",
                     headers=_signed_headers(registrar, "/api/v1/identities", {}), timeout=10)
    if resp.status_code != 200:
        raise CAClientError(error_message(resp))
    return resp.json()
