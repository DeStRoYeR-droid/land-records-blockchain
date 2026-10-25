"""
OpenPGP signatures, implemented from the packet format up (RFC 4880 / RFC 9580, v4).

Why a second signature system when Fabric already signs everything with
X.509? Different audience. A Fabric signature can only be checked by someone
holding the channel's MSP configuration -- a network member. A validation
report has to be checkable OUTSIDE the network: by a citizen, a court, an
auditor answering an RTI request. An OpenPGP detached signature verifies with
standard, free tools and no ledger access:

    gpg --import validator.pub.asc
    gpg --verify report.json.asc report.json

This module writes exactly what GnuPG and OpenPGP.js read, using an Ed25519
key ("EdDSA legacy", public-key algorithm 22, the most widely supported
modern key type). It needs nothing but the `cryptography` package -- no gpg
binary, no gpg-agent.

Packets produced
  public key   tag 6   version 4, created, algorithm 22, curve OID, MPI(0x40 || key)
  user id      tag 13  "LandChain Validator Agent (...) <...>"
  signature    tag 2   version 4: type, algo, hash, hashed subpackets,
                       unhashed subpackets, left 16 bits of the hash, MPI(R), MPI(S)

How a v4 signature is computed
  digest = SHA-256( signed data
                    || version, type, algo, hash, len(hashed), hashed subpackets
                    || 0x04 0xFF || len(everything after "signed data") as 4 bytes )
  EdDSA signs the DIGEST (not the raw data) -- the quirk of algorithm 22.

Fingerprint (v4) = SHA-1(0x99 || 2-byte length || public key packet body).
"""

from __future__ import annotations

import base64
import hashlib
import struct
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ED25519_OID = bytes.fromhex("2B06010401DA470F01")  # 1.3.6.1.4.1.11591.15.1
ALGO_EDDSA = 22
HASH_SHA256 = 8
SIG_BINARY_DOCUMENT = 0x00
SIG_CANONICAL_TEXT = 0x01
SIG_POSITIVE_CERTIFICATION = 0x13

TAG_SIGNATURE, TAG_PUBLIC_KEY, TAG_USER_ID = 2, 6, 13


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def mpi(value: bytes) -> bytes:
    """Multiprecision integer: 2-byte bit count, then the bytes without leading zeros."""
    value = value.lstrip(b"\x00")
    bits = (len(value) - 1) * 8 + value[0].bit_length() if value else 0
    return struct.pack(">H", bits) + value


def packet(tag: int, body: bytes) -> bytes:
    """New-format packet header (RFC 4880 4.2.2)."""
    n = len(body)
    if n < 192:
        header = bytes([0xC0 | tag, n])
    elif n < 8384:
        n -= 192
        header = bytes([0xC0 | tag, (n >> 8) + 192, n & 0xFF])
    else:
        header = bytes([0xC0 | tag, 0xFF]) + struct.pack(">I", n)
    return header + body


def subpacket(kind: int, data: bytes) -> bytes:
    return bytes([1 + len(data), kind]) + data


def crc24(data: bytes) -> int:
    crc = 0xB704CE
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def armor(kind: str, data: bytes, comment: str | None = None) -> str:
    b64 = base64.b64encode(data).decode()
    lines = [f"-----BEGIN PGP {kind}-----"]
    if comment:
        lines.append(f"Comment: {comment}")
    lines.append("")
    lines += [b64[i:i + 64] for i in range(0, len(b64), 64)]
    lines.append("=" + base64.b64encode(crc24(data).to_bytes(3, "big")).decode())
    lines.append(f"-----END PGP {kind}-----")
    return "\n".join(lines) + "\n"


def dearmor(text: str) -> bytes:
    lines = [ln.strip() for ln in text.strip().splitlines()]
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("-----BEGIN PGP"))
        end = next(i for i, ln in enumerate(lines) if ln.startswith("-----END PGP"))
    except StopIteration as exc:
        raise ValueError("not ASCII-armored OpenPGP data") from exc
    body = lines[start + 1:end]
    if "" in body:  # headers end at the first blank line
        body = body[body.index("") + 1:]
    checksum = None
    if body and body[-1].startswith("="):
        checksum = body.pop()
    data = base64.b64decode("".join(body))
    if checksum is not None and base64.b64decode(checksum[1:]) != crc24(data).to_bytes(3, "big"):
        raise ValueError("armor checksum mismatch (the text was altered)")
    return data


def read_packets(data: bytes) -> list[tuple[int, bytes]]:
    """Split binary OpenPGP data into (tag, body). Handles old and new headers."""
    out, i = [], 0
    while i < len(data):
        first = data[i]
        if not first & 0x80:
            raise ValueError("invalid packet header")
        if first & 0x40:  # new format
            tag = first & 0x3F
            o = data[i + 1]
            if o < 192:
                length, hlen = o, 2
            elif o < 224:
                length, hlen = ((o - 192) << 8) + data[i + 2] + 192, 3
            elif o == 255:
                length, hlen = struct.unpack(">I", data[i + 2:i + 6])[0], 6
            else:
                raise ValueError("partial-length packets are not supported")
        else:  # old format
            tag = (first >> 2) & 0x0F
            ltype = first & 0x03
            if ltype == 0:
                length, hlen = data[i + 1], 2
            elif ltype == 1:
                length, hlen = struct.unpack(">H", data[i + 1:i + 3])[0], 3
            elif ltype == 2:
                length, hlen = struct.unpack(">I", data[i + 1:i + 5])[0], 5
            else:
                length, hlen = len(data) - i - 1, 1
        out.append((tag, data[i + hlen:i + hlen + length]))
        i += hlen + length
    return out


def read_mpi(buf: bytes, pos: int) -> tuple[bytes, int]:
    bits = struct.unpack(">H", buf[pos:pos + 2])[0]
    n = (bits + 7) // 8
    return buf[pos + 2:pos + 2 + n], pos + 2 + n


# ---------------------------------------------------------------------------
# Keys and signing
# ---------------------------------------------------------------------------

class SigningKey:
    """An Ed25519 key as an OpenPGP v4 primary key with one user ID."""

    def __init__(self, private: Ed25519PrivateKey, user_id: str, created: int):
        self.private = private
        self.user_id = user_id
        self.created = int(created)
        self.public_raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.public_body = (struct.pack(">BIB", 4, self.created, ALGO_EDDSA)
                            + bytes([len(ED25519_OID)]) + ED25519_OID + mpi(b"\x40" + self.public_raw))
        self.fingerprint = hashlib.sha1(
            b"\x99" + struct.pack(">H", len(self.public_body)) + self.public_body).hexdigest().upper()
        self.key_id = self.fingerprint[-16:]

    @classmethod
    def generate(cls, user_id: str) -> "SigningKey":
        return cls(Ed25519PrivateKey.generate(), user_id, int(time.time()))

    def _signature_packet(self, sig_type: int, prefix: bytes, hashed_subpackets: bytes) -> bytes:
        fields = struct.pack(">BBBB", 4, sig_type, ALGO_EDDSA, HASH_SHA256) \
            + struct.pack(">H", len(hashed_subpackets)) + hashed_subpackets
        trailer = b"\x04\xff" + struct.pack(">I", len(fields))
        digest = hashlib.sha256(prefix + fields + trailer).digest()
        sig = self.private.sign(digest)
        unhashed = subpacket(16, bytes.fromhex(self.key_id))  # issuer key ID
        body = fields + struct.pack(">H", len(unhashed)) + unhashed + digest[:2] + mpi(sig[:32]) + mpi(sig[32:])
        return packet(TAG_SIGNATURE, body)

    def _issuer_fpr(self) -> bytes:
        return subpacket(33, b"\x04" + bytes.fromhex(self.fingerprint))

    def public_key_armored(self, comment: str = "LandChain Agentic AI Validator") -> str:
        uid = self.user_id.encode("utf-8")
        cert_prefix = (b"\x99" + struct.pack(">H", len(self.public_body)) + self.public_body
                       + b"\xb4" + struct.pack(">I", len(uid)) + uid)
        hashed = (subpacket(2, struct.pack(">I", self.created))   # signature creation time
                  + subpacket(27, b"\x03")                          # key flags: certify + sign
                  + subpacket(21, bytes([8, 10, 9]))                # preferred hashes: SHA256, SHA512, SHA384
                  + subpacket(11, bytes([9, 8, 7]))                 # preferred ciphers: AES256, AES192, AES128
                  + subpacket(30, b"\x01")                          # features: MDC
                  + self._issuer_fpr())
        self_sig = self._signature_packet(SIG_POSITIVE_CERTIFICATION, cert_prefix, hashed)
        data = packet(TAG_PUBLIC_KEY, self.public_body) + packet(TAG_USER_ID, uid) + self_sig
        return armor("PUBLIC KEY BLOCK", data, comment=comment)

    def sign_detached(self, document: bytes, when: int | None = None,
                      comment: str = "gpg --verify report.json.asc report.json") -> str:
        """Armored detached signature over the exact bytes of `document`."""
        hashed = subpacket(2, struct.pack(">I", int(when or time.time()))) + self._issuer_fpr()
        sig = self._signature_packet(SIG_BINARY_DOCUMENT, document, hashed)
        return armor("SIGNATURE", sig, comment=comment)

    def sign_cleartext(self, text: str, when: int | None = None) -> str:
        """A cleartext-signed message (RFC 4880 section 7): the text stays readable, the signature follows it.

        One file holds both; `gpg --verify file.asc` checks it. The signature (type 0x01, canonical text) covers
        the text with CRLF line endings and trailing whitespace removed, as the standard requires."""
        hashed = subpacket(2, struct.pack(">I", int(when or time.time()))) + self._issuer_fpr()
        sig = self._signature_packet(SIG_CANONICAL_TEXT, canonical_text(text), hashed)
        body = "\n".join(("- " + line) if line.startswith("-") else line for line in _lines(text))
        return ("-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n" + body + "\n"
                + armor("SIGNATURE", sig, comment="gpg --verify this file"))


def _lines(text: str) -> list[str]:
    return [line.rstrip(" \t\r") for line in text.replace("\r\n", "\n").rstrip("\n").split("\n")]


def canonical_text(text: str) -> bytes:
    """What a canonical-text signature hashes: lines joined by CRLF, trailing blanks removed, no final newline."""
    return "\r\n".join(_lines(text)).encode("utf-8")


def split_cleartext(message: str) -> tuple[str, str]:
    """(the signed text, the armored signature) from a cleartext-signed message. Raises ValueError."""
    message = message.replace("\r\n", "\n")
    head = "-----BEGIN PGP SIGNED MESSAGE-----\n"
    start = message.find(head)
    if start < 0:
        raise ValueError("not a cleartext-signed OpenPGP message")
    rest = message[start + len(head):]
    blank = rest.find("\n\n")
    if blank < 0:
        raise ValueError("cleartext message has no header block")
    for header in rest[:blank].split("\n"):
        if header.startswith("Hash:") and "SHA256" not in header.upper().replace("-", ""):
            raise ValueError(f"unsupported hash header: {header}")
    body = rest[blank + 2:]
    sig_at = body.find("-----BEGIN PGP SIGNATURE-----")
    if sig_at < 0:
        raise ValueError("cleartext message has no signature block")
    text = body[:sig_at]
    if text.endswith("\n"):
        text = text[:-1]                         # the line break before the signature is not signed
    lines = [line[2:] if line.startswith("- ") else line for line in text.split("\n")]
    return "\n".join(lines), body[sig_at:]


def format_fingerprint(fpr: str) -> str:
    """40 hex digits in GnuPG's grouping: 3AF2 91B4 ... (10 groups of 4)."""
    groups = [fpr[i:i + 4] for i in range(0, 40, 4)]
    return " ".join(groups[:5]) + "  " + " ".join(groups[5:])


# ---------------------------------------------------------------------------
# Verification (used by scripts/verify_report.py and the agent's self-check)
# ---------------------------------------------------------------------------

@dataclass
class PublicKey:
    raw: bytes
    created: int
    fingerprint: str
    key_id: str
    user_id: str | None


@dataclass
class Verification:
    valid: bool
    reason: str
    key_id: str | None = None
    fingerprint: str | None = None
    created: int | None = None


def read_public_key(armored: str) -> PublicKey:
    user_id = None
    key = None
    for tag, body in read_packets(dearmor(armored)):
        if tag == TAG_PUBLIC_KEY and key is None:
            version, created, algo = body[0], struct.unpack(">I", body[1:5])[0], body[5]
            if version != 4 or algo != ALGO_EDDSA:
                raise ValueError("only v4 EdDSA (Ed25519) keys are supported here")
            oid_len = body[6]
            if body[7:7 + oid_len] != ED25519_OID:
                raise ValueError("key is not on curve Ed25519")
            point, _ = read_mpi(body, 7 + oid_len)
            if len(point) != 33 or point[0] != 0x40:
                raise ValueError("unexpected EdDSA point encoding")
            fpr = hashlib.sha1(b"\x99" + struct.pack(">H", len(body)) + body).hexdigest().upper()
            key = PublicKey(point[1:], created, fpr, fpr[-16:], None)
        elif tag == TAG_USER_ID and user_id is None:
            user_id = body.decode("utf-8", "replace")
    if key is None:
        raise ValueError("no public key packet found")
    key.user_id = user_id
    return key


def _parse_subpackets(data: bytes) -> dict[int, bytes]:
    out, i = {}, 0
    while i < len(data):
        first = data[i]
        if first < 192:
            length, i = first, i + 1
        elif first < 255:
            length, i = ((first - 192) << 8) + data[i + 1] + 192, i + 2
        else:
            length, i = struct.unpack(">I", data[i + 1:i + 5])[0], i + 5
        out[data[i] & 0x7F] = data[i + 1:i + length]
        i += length
    return out


def verify_cleartext(public_key_armored: str, message: str) -> tuple[Verification, str | None]:
    """Verify a cleartext-signed message. Returns (result, the signed text)."""
    try:
        text, sig = split_cleartext(message)
    except ValueError as exc:
        return Verification(False, str(exc)), None
    return verify_detached(public_key_armored, canonical_text(text), sig, sig_type=SIG_CANONICAL_TEXT), text


def verify_detached(public_key_armored: str, document: bytes, signature_armored: str,
                    sig_type: int = SIG_BINARY_DOCUMENT) -> Verification:
    try:
        key = read_public_key(public_key_armored)
        packets = [b for t, b in read_packets(dearmor(signature_armored)) if t == TAG_SIGNATURE]
    except (ValueError, IndexError, struct.error) as exc:
        return Verification(False, f"unreadable key or signature: {exc}")
    if not packets:
        return Verification(False, "no signature packet")
    body = packets[0]
    version, got_type, algo, hash_algo = body[0], body[1], body[2], body[3]
    if version != 4 or algo != ALGO_EDDSA or hash_algo != HASH_SHA256 or got_type != sig_type:
        kind = "binary document" if sig_type == SIG_BINARY_DOCUMENT else "canonical text"
        return Verification(False, f"unsupported signature (need v4, EdDSA, SHA-256, {kind})")
    hashed_len = struct.unpack(">H", body[4:6])[0]
    hashed = body[6:6 + hashed_len]
    pos = 6 + hashed_len
    unhashed_len = struct.unpack(">H", body[pos:pos + 2])[0]
    unhashed = body[pos + 2:pos + 2 + unhashed_len]
    pos += 2 + unhashed_len
    left16 = body[pos:pos + 2]
    r, pos = read_mpi(body, pos + 2)
    s, pos = read_mpi(body, pos)

    sub = {**_parse_subpackets(unhashed), **_parse_subpackets(hashed)}
    issuer = sub.get(16, b"").hex().upper() or None
    issuer_fpr = sub.get(33, b"")[1:].hex().upper() or None
    created = struct.unpack(">I", sub[2])[0] if 2 in sub else None
    if (issuer_fpr and issuer_fpr != key.fingerprint) or (issuer and issuer != key.key_id):
        return Verification(False, f"signed by a different key ({issuer_fpr or issuer})",
                            issuer, issuer_fpr, created)

    fields = body[:6 + hashed_len]
    digest = hashlib.sha256(document + fields + b"\x04\xff" + struct.pack(">I", len(fields))).digest()
    if digest[:2] != left16:
        return Verification(False, "BAD signature: the document does not match (hash prefix differs)",
                            key.key_id, key.fingerprint, created)
    try:
        Ed25519PublicKey.from_public_bytes(key.raw).verify(r.rjust(32, b"\x00") + s.rjust(32, b"\x00"), digest)
    except InvalidSignature:
        return Verification(False, "BAD signature", key.key_id, key.fingerprint, created)
    return Verification(True, "Good signature", key.key_id, key.fingerprint, created)
