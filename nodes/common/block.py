"""
Blocks and transactions: the shapes that travel between nodes.

A block is three parts, as in Fabric:

    header    {number, previousHash, dataHash}   <- this is what gets hashed
    data      the transactions (or, for a config block, the channel config)
    metadata  who cut the block and its signature, Raft position, and -- added
              by each peer after validation -- one validation code per tx

    block hash = SHA-256(canonical(header))

Because every header contains the previous block's hash, changing any old
transaction changes that block's dataHash, hence its hash, hence every later
block's previousHash. That is the whole "chain".

Metadata is deliberately outside the hash: a peer writes its validation
results there without changing the block's identity.
"""

from __future__ import annotations

import os
from typing import Any

from .canonical import b64e, canonical, hash_obj, sha256_hex

GENESIS_PREVIOUS_HASH = "0" * 64


def make_block(number: int, previous_hash: str, data: dict[str, Any],
               metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    header = {"number": number, "previousHash": previous_hash, "dataHash": hash_obj(data)}
    return {"header": header, "data": data, "metadata": metadata or {}}


def block_hash(block: dict[str, Any]) -> str:
    return hash_obj(block["header"])


def header_bytes(block: dict[str, Any]) -> bytes:
    return canonical(block["header"])


def check_block_integrity(block: dict[str, Any], expected_number: int, expected_previous_hash: str) -> str | None:
    """Return an error string if the block does not extend the chain correctly."""
    header = block.get("header", {})
    if header.get("number") != expected_number:
        return f"expected block {expected_number}, got {header.get('number')}"
    if header.get("previousHash") != expected_previous_hash:
        return f"block {expected_number} does not point at the previous block's hash"
    if header.get("dataHash") != hash_obj(block.get("data")):
        return f"block {expected_number} data does not match its dataHash (tampered?)"
    return None


def is_config_block(block: dict[str, Any]) -> bool:
    return block.get("data", {}).get("kind") == "config"


# ---------------------------------------------------------------------------
# Proposals and transaction IDs
# ---------------------------------------------------------------------------

def new_nonce() -> str:
    return b64e(os.urandom(24))


def compute_tx_id(nonce_b64: str, creator: dict[str, str]) -> str:
    """Fabric's rule: txId = SHA-256(nonce || serialized creator)."""
    return sha256_hex(nonce_b64.encode() + creator["mspId"].encode() + creator["cert"].encode())


def envelope_tx_id(envelope: dict[str, Any]) -> str | None:
    try:
        if envelope.get("type") == "config_update":
            return envelope["update"]["txId"]
        return envelope["signedProposal"]["proposal"]["txId"]
    except (KeyError, TypeError):
        return None


def envelope_creator(envelope: dict[str, Any]) -> dict[str, str] | None:
    try:
        if envelope.get("type") == "config_update":
            return envelope["update"]["creator"]
        return envelope["signedProposal"]["proposal"]["creator"]
    except (KeyError, TypeError):
        return None


def envelope_signed_part(envelope: dict[str, Any]) -> bytes:
    """What the submitting client signs: everything except its own signature."""
    return canonical({k: v for k, v in envelope.items() if k != "signature"})


# Validation codes (same names as Fabric's TxValidationCode).
VALID = "VALID"
DUPLICATE_TXID = "DUPLICATE_TXID"
BAD_PAYLOAD = "BAD_PAYLOAD"
BAD_CREATOR_SIGNATURE = "BAD_CREATOR_SIGNATURE"
BAD_PROPOSAL_TXID = "BAD_PROPOSAL_TXID"
INVALID_ENDORSER_TRANSACTION = "INVALID_ENDORSER_TRANSACTION"
ENDORSEMENT_POLICY_FAILURE = "ENDORSEMENT_POLICY_FAILURE"
MVCC_READ_CONFLICT = "MVCC_READ_CONFLICT"
PHANTOM_READ_CONFLICT = "PHANTOM_READ_CONFLICT"
INVALID_CHAINCODE = "INVALID_CHAINCODE"
INVALID_CONFIG_TRANSACTION = "INVALID_CONFIG_TRANSACTION"
TARGET_CHAIN_NOT_FOUND = "TARGET_CHAIN_NOT_FOUND"
