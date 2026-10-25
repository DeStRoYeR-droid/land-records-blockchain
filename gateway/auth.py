"""
Who is calling the gateway.

Officers log in with a username and password (demo: the password is the
username, or LANDCHAIN_PASSWORD_<USER>). The gateway then signs THEIR
transactions with THEIR CA-issued key -- the chaincode sees clerk1's
certificate, not a shared service account.

Citizens prove two things to log in:
  * they control the DigiLocker account (mock OTP 123456), and
  * they hold the private key of the did:key that Registration bound to
    their ownerRef on the ledger (they sign a one-time challenge).

Sessions are HMAC-signed tokens (data/gateway/secret), sent as
`Authorization: Bearer <token>`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Header, HTTPException

from nodes.common import config

TOKEN_TTL = 8 * 3600
SERVICE_ROLES = {"portal", "agent"}  # identities the gateway/agent use, not people

TITLES = {
    "registrar": ("Sub-Registrar", "Registers parcels, issues deeds, forwards files to Revenue"),
    "clerk": ("Registration Clerk", "Opens transfer files from citizen-signed intents, anchors documents"),
    "verifier": ("Revenue Verifier", "Re-verifies documents independently, forwards to the Collectorate"),
    "surveyor": ("Mandal Surveyor", "Measures the land and records the survey"),
    "collector": ("District Collector", "Approves or rejects transfers -- the only decision that moves ownership"),
}


def _secret() -> bytes:
    path = config.data_dir("gateway") / "secret"
    if not path.exists():
        path.write_bytes(secrets.token_bytes(32))
        os.chmod(path, 0o600)
    return path.read_bytes()


SECRET = _secret()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(claims: dict[str, Any]) -> str:
    body = _b64(json.dumps({**claims, "exp": int(time.time()) + TOKEN_TTL}).encode())
    sig = _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_token(token: str) -> dict[str, Any] | None:
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _b64(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())):
            return None
        claims = json.loads(_unb64(body))
        return claims if claims.get("exp", 0) > time.time() else None
    except (ValueError, json.JSONDecodeError):
        return None


def officers() -> dict[str, dict[str, Any]]:
    out = {}
    for msp_id, org in config.topology()["organizations"].items():
        for user, role in (org.get("users") or {}).items():
            if role in SERVICE_ROLES:
                continue
            title, duty = TITLES.get(role, (role.title(), ""))
            out[user] = {"username": user, "mspId": msp_id, "org": org["name"], "dept": org.get("dept"),
                         "role": role, "title": title, "duty": duty}
    return out


def password_ok(username: str, password: str) -> bool:
    expected = os.environ.get(f"LANDCHAIN_PASSWORD_{username.upper()}", username)
    return hmac.compare_digest(expected.encode(), password.encode())


@dataclass
class Principal:
    kind: str                      # officer | citizen
    claims: dict[str, Any]

    @property
    def role(self) -> str | None:
        return self.claims.get("role")

    def is_officer(self, *roles: str) -> bool:
        return self.kind == "officer" and (not roles or self.claims.get("role") in roles)


def _from_header(authorization: str | None) -> Principal | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    claims = read_token(authorization[7:].strip())
    if not claims:
        return None
    return Principal(claims.get("kind", ""), claims)


def optional_principal(authorization: str | None = Header(None)) -> Principal | None:
    return _from_header(authorization)


def any_principal(authorization: str | None = Header(None)) -> Principal:
    p = _from_header(authorization)
    if not p:
        raise HTTPException(401, "Sign in first (missing or expired session)")
    return p


def officer(authorization: str | None = Header(None)) -> Principal:
    p = any_principal(authorization)
    if p.kind != "officer":
        raise HTTPException(403, "This needs a government officer's session")
    return p


def citizen(authorization: str | None = Header(None)) -> Principal:
    p = any_principal(authorization)
    if p.kind != "citizen":
        raise HTTPException(403, "This needs a citizen's session")
    return p


def need_role(p: Principal, *roles: str) -> None:
    if not p.is_officer(*roles):
        raise HTTPException(403, f"Only {' / '.join(TITLES.get(r, (r,))[0] for r in roles)} can do this "
                                 f"(you are signed in as {p.claims.get('title', p.kind)})")


class Challenges:
    """One-time nonces for DID proofs (5 minutes)."""

    def __init__(self):
        self.items: dict[str, tuple[str, str, float]] = {}

    def issue(self, purpose: str, subject: str) -> str:
        now = time.time()
        self.items = {k: v for k, v in self.items.items() if v[2] > now}
        nonce = secrets.token_urlsafe(24)
        self.items[nonce] = (purpose, subject, now + 300)
        return nonce

    def take(self, nonce: str, purpose: str, subject: str) -> bool:
        item = self.items.pop(nonce, None)
        return bool(item and item[0] == purpose and item[1] == subject and item[2] > time.time())
