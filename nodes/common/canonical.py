"""
Canonical bytes: the single representation every node agrees on.

Hashes and signatures are computed over *bytes*, not objects. If two nodes
serialised the same object differently (key order, whitespace, escaping), the
hashes would differ and every signature check would fail. canonical() pins the
bytes down: keys sorted, no whitespace, UTF-8, no NaN/Infinity.

Fabric uses protobuf for the same reason (a deterministic wire format); this
project uses canonical JSON so blocks stay human-readable.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any


def canonical(obj: Any) -> bytes:
    """Deterministic JSON bytes for hashing and signing."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    """Hex SHA-256 of an object's canonical bytes."""
    return sha256_hex(canonical(obj))


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)
