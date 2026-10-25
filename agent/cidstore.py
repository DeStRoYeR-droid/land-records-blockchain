"""
Content-addressed storage with IPFS-compatible CIDs.

A CID names bytes by their hash, so a CID can't point at different content
than it was made from. For one small file (< 256 KiB) the CID IPFS gives with

    ipfs add --cid-version=1 --raw-leaves report.json

is CIDv1(raw codec 0x55, sha2-256 multihash of the bytes), written in
base32 -- "bafkrei...". This store computes exactly that, so if you add the
same report to a real IPFS node you get the same CID.

Optionally mirrors every object to a real IPFS node: set
IPFS_API_URL=http://127.0.0.1:5001 and run `ipfs daemon`.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import httpx

RAW_CODEC = 0x55
SHA2_256 = 0x12


def cid_v1_raw(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    binary = bytes([0x01, RAW_CODEC, SHA2_256, len(digest)]) + digest
    return "b" + base64.b32encode(binary).decode("ascii").lower().rstrip("=")


def digest_of(cid: str) -> bytes:
    if not cid.startswith("b"):
        raise ValueError("only base32 CIDv1 is supported")
    raw = base64.b32decode(cid[1:].upper() + "=" * (-len(cid[1:]) % 8))
    if raw[:4] != bytes([0x01, RAW_CODEC, SHA2_256, 32]):
        raise ValueError("not a CIDv1 raw/sha2-256 CID")
    return raw[4:]


class CIDStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ipfs_api = os.environ.get("IPFS_API_URL")

    def put(self, data: bytes) -> str:
        cid = cid_v1_raw(data)
        path = self.root / cid
        if not path.exists():
            path.write_bytes(data)
        if self.ipfs_api:
            self._mirror(data, cid)
        return cid

    def get(self, cid: str) -> bytes:
        path = self.root / cid
        if not path.exists():
            raise KeyError(cid)
        data = path.read_bytes()
        if hashlib.sha256(data).digest() != digest_of(cid):  # self-verifying read
            raise ValueError(f"stored object for {cid} is corrupt")
        return data

    def has(self, cid: str) -> bool:
        return (self.root / cid).exists()

    def _mirror(self, data: bytes, cid: str) -> None:
        try:
            resp = httpx.post(f"{self.ipfs_api}/api/v0/add", params={"cid-version": 1, "raw-leaves": "true", "pin": "true"},
                              files={"file": ("object", data)}, timeout=10)
            got = resp.json().get("Hash")
            if got != cid:
                raise ValueError(f"IPFS returned {got}, expected {cid}")
        except Exception as exc:  # mirroring is best-effort
            import logging
            logging.getLogger("cidstore").warning("IPFS mirror failed for %s: %s", cid, exc)
