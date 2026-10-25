"""
Membership Service Provider (MSP).

An MSP answers one question: "does this certificate belong to organisation X,
and in what capacity?"

* It trusts the organisation's root CA certificate. A certificate belongs to
  the MSP only if that root signed it.
* The capacity (client, peer, admin, orderer) comes from the certificate's OU,
  which the CA stamped at enrollment from the identity's registered type
  ("Node OUs" in Fabric).

The channel's genesis block carries every organisation's root certificate, so
every node can check every other node's identity without asking anyone.

On the wire an identity is serialised as {"mspId": "...", "cert": "<PEM>"},
the equivalent of Fabric's SerializedIdentity.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature

from . import crypto


class MSPError(Exception):
    """The identity is not a valid member of the organisation it claims."""


@dataclass(frozen=True)
class Identity:
    msp_id: str
    cert_pem: str
    cert: x509.Certificate = field(compare=False, hash=False, repr=False)

    @property
    def name(self) -> str:
        return crypto.cert_common_name(self.cert)

    @property
    def ou(self) -> str | None:
        return crypto.cert_node_ou(self.cert)

    @property
    def attrs(self) -> dict[str, str]:
        return crypto.cert_attributes(self.cert)

    def attr(self, name: str) -> str | None:
        return self.attrs.get(name)

    def id_string(self) -> str:
        return crypto.fabric_id_string(self.cert)

    def verify(self, data: bytes, signature_b64: str) -> bool:
        return crypto.verify(self.cert.public_key(), data, signature_b64)

    def wire(self) -> dict[str, str]:
        return {"mspId": self.msp_id, "cert": self.cert_pem}

    def summary(self) -> dict[str, Any]:
        return {"mspId": self.msp_id, "name": self.name, "ou": self.ou,
                "role": self.attr("role"), "dept": self.attr("dept")}


class SigningIdentity:
    """An identity we hold the private key for: it can sign proposals and blocks.

    Loaded from a Fabric-style MSP folder:
        msp/signcerts/cert.pem   the X.509 certificate
        msp/keystore/*           the PKCS#8 private key (never leaves this machine)
    """

    def __init__(self, msp_id: str, cert_pem: str, key: crypto.PrivateKey):
        self.msp_id = msp_id
        self.cert_pem = cert_pem
        self.cert = crypto.load_cert(cert_pem)
        self.key = key
        self.identity = Identity(msp_id, cert_pem, self.cert)

    @classmethod
    def from_msp_dir(cls, msp_dir, msp_id: str) -> "SigningIdentity":
        from pathlib import Path

        msp_dir = Path(msp_dir)
        cert_pem = (msp_dir / "signcerts" / "cert.pem").read_text()
        key_files = sorted((msp_dir / "keystore").glob("*"))
        if not key_files:
            raise FileNotFoundError(f"no private key in {msp_dir / 'keystore'}")
        key = crypto.load_private_key(key_files[0].read_bytes())
        return cls(msp_id, cert_pem, key)

    @property
    def name(self) -> str:
        return self.identity.name

    def sign(self, data: bytes) -> str:
        return crypto.sign(self.key, data)

    def wire(self) -> dict[str, str]:
        return self.identity.wire()


class MSP:
    """One organisation's view: its root CA and the rules for its members."""

    def __init__(self, msp_id: str, root_certs_pem: list[str]):
        if not root_certs_pem:
            raise ValueError(f"MSP {msp_id} has no root certificates")
        self.msp_id = msp_id
        self.roots = [crypto.load_cert(pem) for pem in root_certs_pem]

    def validate(self, cert: x509.Certificate, at: dt.datetime | None = None) -> None:
        """Raise MSPError unless `cert` is a member of this organisation."""
        issued_by_root = False
        for root in self.roots:
            try:
                cert.verify_directly_issued_by(root)
                issued_by_root = True
                break
            except (ValueError, TypeError, InvalidSignature):  # wrong issuer or bad signature
                continue
        if not issued_by_root:
            raise MSPError(f"certificate '{crypto.cert_common_name(cert)}' was not issued by {self.msp_id}'s CA")
        if crypto.cert_node_ou(cert) is None:
            raise MSPError(f"certificate '{crypto.cert_common_name(cert)}' has no node OU (client/peer/admin/orderer)")
        if at is not None:
            if at < cert.not_valid_before_utc or at > cert.not_valid_after_utc:
                raise MSPError(f"certificate '{crypto.cert_common_name(cert)}' is not valid at {crypto.iso(at)}")


class MSPManager:
    """All MSPs on the channel, keyed by MSP ID."""

    def __init__(self, msps: dict[str, MSP]):
        self.msps = msps

    @classmethod
    def from_channel_config(cls, config: dict[str, Any]) -> "MSPManager":
        msps: dict[str, MSP] = {}
        orderer_org = config["orderer"]["org"]
        msps[orderer_org["mspId"]] = MSP(orderer_org["mspId"], orderer_org["rootCerts"])
        for msp_id, org in config["orgs"].items():
            msps[msp_id] = MSP(msp_id, org["rootCerts"])
        return cls(msps)

    def deserialize(self, wire: dict[str, str], at: dt.datetime | None = None) -> Identity:
        """Turn {"mspId", "cert"} into a validated Identity or raise MSPError."""
        if not isinstance(wire, dict) or "mspId" not in wire or "cert" not in wire:
            raise MSPError("malformed identity")
        msp = self.msps.get(wire["mspId"])
        if msp is None:
            raise MSPError(f"unknown MSP '{wire['mspId']}' (not a member of this channel)")
        try:
            cert = crypto.load_cert(wire["cert"])
        except ValueError as exc:
            raise MSPError(f"unparseable certificate: {exc}") from exc
        msp.validate(cert, at)
        return Identity(wire["mspId"], wire["cert"], cert)

    def application_orgs(self, config: dict[str, Any]) -> list[str]:
        return sorted(config["orgs"].keys())
