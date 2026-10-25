"""
CA server: a small REST API in front of CertificateAuthority.

  GET  /api/v1/cainfo       the CA's root certificate (anyone may fetch it)
  POST /api/v1/register     registrar-signed request -> new identity record
  POST /api/v1/enroll       HTTP Basic name:secret + CSR -> signed certificate
  GET  /api/v1/identities   who has been registered (registrar-signed request)

A register request is authenticated by a signature, not a password: the
registrar sends its certificate and signs the request body with its private
key, so the CA knows the request came from an enrolled registrar.
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import Header, Request
from pydantic import BaseModel

from ..common import config, crypto, log
from ..common.canonical import canonical
from ..common.web import NodeError, create_app
from .authority import CAError, CertificateAuthority

logger = log.get("ca")


class RegisterBody(BaseModel):
    name: str
    secret: str
    type: str
    attrs: dict[str, str] = {}


class EnrollBody(BaseModel):
    csr: str


def _auth_registrar(ca: CertificateAuthority, body: dict[str, Any], path: str,
                    cert_b64: str | None, sig: str | None) -> str:
    if not cert_b64 or not sig:
        raise NodeError(401, "registrar certificate and signature headers are required")
    cert_pem = base64.b64decode(cert_b64).decode()
    cert = crypto.load_cert(cert_pem)
    signed = canonical({"path": path, "body": body})
    if not crypto.verify(cert.public_key(), signed, sig):
        raise NodeError(401, "registrar signature does not verify")
    try:
        cert.verify_directly_issued_by(ca.cert)
    except Exception as exc:
        raise NodeError(401, f"registrar certificate was not issued by {ca.ca_name}") from exc
    return cert_pem


def build_app(ca_name: str):
    spec = config.node(ca_name)
    msp_id = spec["org"]
    ca = CertificateAuthority(ca_name, msp_id, config.org_domain(msp_id), config.ca_dir(ca_name))
    logger.info("CA %s for %s ready (root CN=%s)", ca_name, msp_id, crypto.cert_common_name(ca.cert))

    app = create_app(f"LandChain CA · {ca_name}",
                     f"Certificate Authority for {msp_id}. Registers identities and signs CSRs.")

    @app.get("/health")
    def health():
        return {"status": "ok", "ca": ca_name}

    @app.get("/api/v1/cainfo")
    def cainfo():
        return ca.info()

    @app.post("/api/v1/register")
    def register(body: RegisterBody, x_registrar_cert: str | None = Header(None),
                 x_registrar_signature: str | None = Header(None)):
        payload = body.model_dump()
        cert_pem = _auth_registrar(ca, payload, "/api/v1/register", x_registrar_cert, x_registrar_signature)
        try:
            result = ca.register(cert_pem, body.name, body.secret, body.type, body.attrs)
        except CAError as exc:
            raise NodeError(exc.status, exc.message) from exc
        logger.info("registered %s (type=%s attrs=%s)", body.name, body.type, body.attrs)
        return result

    @app.post("/api/v1/enroll")
    def enroll(body: EnrollBody, request: Request):
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("basic "):
            raise NodeError(401, "HTTP Basic credentials (enrollment id:secret) required")
        try:
            name, secret = base64.b64decode(auth[6:]).decode().split(":", 1)
        except Exception as exc:
            raise NodeError(401, "malformed Basic credentials") from exc
        try:
            cert_pem = ca.enroll(name, secret, body.csr)
        except CAError as exc:
            raise NodeError(exc.status, exc.message) from exc
        logger.info("enrolled %s", name)
        return {"cert": cert_pem, "caCert": crypto.cert_to_pem(ca.cert)}

    @app.get("/api/v1/identities")
    def identities(x_registrar_cert: str | None = Header(None),
                   x_registrar_signature: str | None = Header(None)):
        _auth_registrar(ca, {}, "/api/v1/identities", x_registrar_cert, x_registrar_signature)
        return ca.list_identities()

    return app
