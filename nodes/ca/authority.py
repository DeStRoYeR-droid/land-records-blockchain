"""The CA's logic: root key, identity registry, certificate issuance."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ..common import crypto

IDENTITY_TYPES = ("client", "peer", "admin", "orderer")
CERT_YEARS = 5


class CAError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _hash_secret(secret: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, 100_000).hex()


class CertificateAuthority:
    def __init__(self, ca_name: str, msp_id: str, org_domain: str, home: Path):
        self.ca_name = ca_name
        self.msp_id = msp_id
        self.org_domain = org_domain
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True)
        self.cert_path = self.home / "ca-cert.pem"
        self.key_path = self.home / "ca-key.pem"
        self._ensure_root()
        self.db = sqlite3.connect(self.home / "registry.db", check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS identities (name TEXT PRIMARY KEY, salt BLOB, secret TEXT,"
            " type TEXT, attrs TEXT, enrollments INTEGER DEFAULT 0, registered_by TEXT, registered_at TEXT)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS issued (serial TEXT PRIMARY KEY, name TEXT, issued_at TEXT, cert TEXT)")
        self.db.commit()
        self._ensure_bootstrap_admin()

    # -- root of trust ------------------------------------------------------

    def _ensure_root(self) -> None:
        if self.cert_path.exists() and self.key_path.exists():
            self.key = crypto.load_private_key(self.key_path.read_bytes())
            self.cert = crypto.load_cert(self.cert_path.read_bytes())
            return
        self.key = crypto.generate_private_key()
        name = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Telangana"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Warangal"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, self.org_domain),
            x509.NameAttribute(NameOID.COMMON_NAME, self.ca_name),
        ])
        now = crypto.utcnow()
        ski = x509.SubjectKeyIdentifier.from_public_key(self.key.public_key())
        self.cert = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=365 * 15))
            .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(ski, critical=False)
            .sign(self.key, hashes.SHA256())
        )
        self.key_path.write_bytes(crypto.private_key_to_pem(self.key))
        os.chmod(self.key_path, 0o600)
        self.cert_path.write_text(crypto.cert_to_pem(self.cert))

    def _ensure_bootstrap_admin(self) -> None:
        """The CA's first identity (like fabric-ca-server -b admin:adminpw)."""
        if self._get("admin") is None:
            self._insert("admin", "adminpw", "admin",
                         {"hf.Registrar.Roles": "client,peer,admin,orderer"}, registered_by="bootstrap")

    # -- registry -----------------------------------------------------------

    def _get(self, name: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT name, salt, secret, type, attrs, enrollments FROM identities WHERE name=?", (name,)).fetchone()
        if not row:
            return None
        return {"name": row[0], "salt": row[1], "secret": row[2], "type": row[3],
                "attrs": json.loads(row[4]), "enrollments": row[5]}

    def _insert(self, name: str, secret: str, id_type: str, attrs: dict[str, str], registered_by: str) -> None:
        salt = os.urandom(16)
        self.db.execute(
            "INSERT INTO identities (name, salt, secret, type, attrs, registered_by, registered_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (name, salt, _hash_secret(secret, salt), id_type, json.dumps(attrs), registered_by,
             crypto.iso(crypto.utcnow())))
        self.db.commit()

    def register(self, registrar_cert_pem: str, name: str, secret: str, id_type: str,
                 attrs: dict[str, str]) -> dict[str, Any]:
        """Create an identity record. Only an enrolled registrar of THIS CA may do it."""
        registrar = crypto.load_cert(registrar_cert_pem)
        try:
            registrar.verify_directly_issued_by(self.cert)
        except Exception as exc:  # wrong issuer / bad signature
            raise CAError(401, f"registrar certificate was not issued by {self.ca_name}") from exc
        roles = crypto.cert_attributes(registrar).get("hf.Registrar.Roles", "")
        if id_type not in roles.split(","):
            raise CAError(403, f"{crypto.cert_common_name(registrar)} may not register type '{id_type}'")
        if id_type not in IDENTITY_TYPES:
            raise CAError(400, f"type must be one of {IDENTITY_TYPES}")
        if not name or "/" in name or len(name) > 64:
            raise CAError(400, "invalid identity name")
        if self._get(name) is not None:
            raise CAError(409, f"Identity '{name}' is already registered")
        self._insert(name, secret, id_type, attrs, registered_by=crypto.cert_common_name(registrar))
        return {"name": name, "type": id_type, "attrs": attrs}

    # -- enrollment ---------------------------------------------------------

    def enroll(self, name: str, secret: str, csr_pem: str) -> str:
        """Check the secret, check the CSR's self-signature, issue a certificate."""
        record = self._get(name)
        if record is None or not hmac.compare_digest(record["secret"], _hash_secret(secret, record["salt"])):
            raise CAError(401, "authentication failure: unknown identity or wrong secret")
        try:
            csr = x509.load_pem_x509_csr(csr_pem.encode())
        except ValueError as exc:
            raise CAError(400, f"bad CSR: {exc}") from exc
        if not csr.is_signature_valid:
            raise CAError(400, "CSR signature is invalid (the requester must sign it with its own key)")

        now = crypto.utcnow()
        subject = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Telangana"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, self.org_domain),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, record["type"]),
            x509.NameAttribute(NameOID.COMMON_NAME, name),
        ])
        attrs = {"hf.EnrollmentID": name, "hf.Type": record["type"], "hf.Affiliation": ""}
        attrs.update(record["attrs"])
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self.cert.subject)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=365 * CERT_YEARS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH,
                                                  ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(csr.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.key.public_key()),
                           critical=False)
            # Fabric CA's attribute extension: raw JSON, read by the chaincode.
            .add_extension(x509.UnrecognizedExtension(
                crypto.FABRIC_ATTRS_OID, json.dumps({"attrs": attrs}, sort_keys=True).encode()), critical=False)
        )
        try:
            san = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName)
            builder = builder.add_extension(san.value, critical=False)
        except x509.ExtensionNotFound:
            pass
        cert = builder.sign(self.key, hashes.SHA256())
        pem = crypto.cert_to_pem(cert)
        self.db.execute("UPDATE identities SET enrollments = enrollments + 1 WHERE name=?", (name,))
        self.db.execute("INSERT INTO issued (serial, name, issued_at, cert) VALUES (?,?,?,?)",
                        (format(cert.serial_number, "x"), name, crypto.iso(now), pem))
        self.db.commit()
        return pem

    def info(self) -> dict[str, Any]:
        count = self.db.execute("SELECT COUNT(*) FROM identities").fetchone()[0]
        issued = self.db.execute("SELECT COUNT(*) FROM issued").fetchone()[0]
        return {"caName": self.ca_name, "mspId": self.msp_id, "caCert": crypto.cert_to_pem(self.cert),
                "identities": count, "certificatesIssued": issued}

    def list_identities(self) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT name, type, attrs, enrollments, registered_by, registered_at FROM identities ORDER BY rowid").fetchall()
        return [{"name": r[0], "type": r[1], "attrs": json.loads(r[2]), "enrollments": r[3],
                 "registeredBy": r[4], "registeredAt": r[5]} for r in rows]
