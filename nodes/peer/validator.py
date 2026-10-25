"""
Validator: the VALIDATE step, run by every peer on every block.

For each transaction, in block order:

  1. DUPLICATE_TXID        this txId is already on the ledger (or earlier in the block)
  2. BAD_CREATOR_SIGNATURE the submitter's certificate or signatures don't check out
  3. BAD_PROPOSAL_TXID     txId != SHA-256(nonce || creator)
  4. INVALID_ENDORSER_TRANSACTION  result doesn't belong to this proposal, or the
                           chaincode returned an error
  5. INVALID_CHAINCODE     endorsed with a different chaincode version than the
                           channel's committed definition
  6. ENDORSEMENT_POLICY_FAILURE  not enough valid endorser signatures over THIS
                           result (e.g. only two departments endorsed)
  7. MVCC_READ_CONFLICT    a key it read has changed since it was simulated
  8. PHANTOM_READ_CONFLICT a range it scanned now returns different keys

A transaction that fails stays in the block (the block is the agreed order of
events and can't be edited) but its writes are not applied. Its code is
recorded in the peer's copy of the block metadata.

Every peer runs the same checks on the same blocks in the same order, so all
peers end up with identical world states -- no further communication needed.
"""

from __future__ import annotations

from typing import Any

from ..common import block as blk
from ..common import crypto, policy
from ..common.canonical import canonical, hash_obj
from ..common.msp import Identity, MSPError, MSPManager
from .ledger import Ledger, Version


class TxRejected(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class IdentityCache:
    """Certificate validation is the expensive part; remember the answers."""

    def __init__(self, msps: MSPManager):
        self.msps = msps
        self.cache: dict[tuple[str, str], Identity | str] = {}

    def get(self, wire: dict[str, str], at: Any) -> Identity:
        key = (wire.get("mspId", ""), wire.get("cert", ""))
        cached = self.cache.get(key)
        if cached is None:
            try:
                cached = self.msps.deserialize(wire)
            except MSPError as exc:
                cached = str(exc)
            if len(self.cache) > 2000:
                self.cache.clear()
            self.cache[key] = cached
        if isinstance(cached, str):
            raise MSPError(cached)
        if at is not None and (at < cached.cert.not_valid_before_utc or at > cached.cert.not_valid_after_utc):
            raise MSPError(f"certificate '{cached.name}' was not valid at {crypto.iso(at)}")
        return cached


class BlockValidator:
    def __init__(self, ledger: Ledger, channel_config: dict[str, Any], identities: IdentityCache):
        self.ledger = ledger
        self.config = channel_config
        self.identities = identities

    # -- endorser transactions --------------------------------------------------

    def validate_transactions(self, block: dict[str, Any]) -> dict[str, Any]:
        number = block["header"]["number"]
        txs = block["data"]["transactions"]
        overlay: dict[str, tuple[str | None, Version]] = {}  # writes of earlier valid txs in this block
        seen: set[str] = set()
        codes: list[str] = []
        details: list[str] = []
        tx_ids: list[str | None] = []
        updates: list[dict[str, Any]] = []
        summaries: list[dict[str, Any]] = []
        for i, env in enumerate(txs):
            txid = blk.envelope_tx_id(env)
            tx_ids.append(txid)
            summary: dict[str, Any] = {"txId": txid, "index": i}
            try:
                prop, result, creator = self._validate(env, txid, seen, overlay)
                code, detail = blk.VALID, ""
                for w in result["rwset"]["writes"]:
                    overlay[w["key"]] = (None if w.get("delete") else w["value"], (number, i))
                updates.append({"txIndex": i, "txId": txid, "timestamp": prop["timestamp"],
                                "writes": result["rwset"]["writes"]})
                summary.update(fn=prop["fn"], creator=creator.summary(), timestamp=prop["timestamp"],
                               event=result.get("event"))
            except TxRejected as rej:
                code, detail = rej.code, rej.detail
                try:
                    p = env["signedProposal"]["proposal"]
                    summary.update(fn=p.get("fn"), timestamp=p.get("timestamp"),
                                   creator={"mspId": p["creator"]["mspId"],
                                            "name": crypto.cert_common_name(crypto.load_cert(p["creator"]["cert"]))})
                except Exception:
                    pass
            if txid:
                seen.add(txid)
            summary.update(code=code, detail=detail)
            codes.append(code)
            details.append(detail)
            summaries.append(summary)
        return {"codes": codes, "details": details, "txIds": tx_ids, "updates": updates, "txs": summaries}

    def _validate(self, env: dict[str, Any], txid: str | None, seen: set[str],
                  overlay: dict[str, tuple[str | None, Version]]):
        if env.get("type") != "endorser_transaction" or not txid:
            raise TxRejected(blk.BAD_PAYLOAD, "not an endorser transaction")
        if txid in seen or self.ledger.has_tx(txid):
            raise TxRejected(blk.DUPLICATE_TXID, f"txId {txid[:16]}... was already used")
        try:
            prop = env["signedProposal"]["proposal"]
            result = env["result"]
            endorsements = env["endorsements"]
            ts = crypto.parse_iso(prop["timestamp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TxRejected(blk.BAD_PAYLOAD, f"malformed transaction: {exc}") from exc

        # 2. creator and its two signatures (proposal + envelope)
        try:
            creator = self.identities.get(prop["creator"], ts)
        except MSPError as exc:
            raise TxRejected(blk.BAD_CREATOR_SIGNATURE, str(exc)) from exc
        if not creator.verify(canonical(prop), env["signedProposal"]["signature"]):
            raise TxRejected(blk.BAD_CREATOR_SIGNATURE, "proposal signature invalid")
        if not creator.verify(blk.envelope_signed_part(env), env.get("signature", "")):
            raise TxRejected(blk.BAD_CREATOR_SIGNATURE, "envelope signature invalid")
        if creator.msp_id not in self.config["orgs"] or creator.ou not in ("client", "admin"):
            raise TxRejected(blk.BAD_CREATOR_SIGNATURE, "creator does not satisfy the channel Writers policy")

        # 3. txId binding
        if blk.compute_tx_id(prop["nonce"], prop["creator"]) != txid:
            raise TxRejected(blk.BAD_PROPOSAL_TXID, "txId is not SHA-256(nonce || creator)")

        # 4. the result belongs to this proposal and succeeded
        if result.get("txId") != txid or result.get("proposalHash") != hash_obj(prop):
            raise TxRejected(blk.INVALID_ENDORSER_TRANSACTION, "endorsed result does not match the proposal")
        if result.get("response", {}).get("status") != 200:
            raise TxRejected(blk.INVALID_ENDORSER_TRANSACTION, "chaincode response was not successful")

        # 5. the committed chaincode definition
        cc = result.get("chaincode", {})
        definition = self.config.get("chaincodes", {}).get(cc.get("name"))
        if definition is None or cc.get("name") != prop.get("chaincode"):
            raise TxRejected(blk.INVALID_CHAINCODE, f"unknown chaincode {cc.get('name')}")
        if cc.get("packageHash") != definition["packageHash"] or cc.get("sequence") != definition["sequence"]:
            raise TxRejected(blk.INVALID_CHAINCODE,
                             f"endorsed with {cc.get('name')} sequence {cc.get('sequence')}, "
                             f"but the channel is at sequence {definition['sequence']}")

        # 6. endorsement policy over valid signatures on THIS exact result
        signed_bytes = canonical(result)
        signers: list[Identity] = []
        for e in endorsements:
            try:
                endorser = self.identities.get(e["endorser"], ts)
            except (MSPError, KeyError):
                continue
            if endorser.verify(signed_bytes, e.get("signature", "")):
                signers.append(endorser)
        rule = policy.parse(definition["endorsementPolicy"])
        if not policy.evaluate(rule, signers):
            raise TxRejected(blk.ENDORSEMENT_POLICY_FAILURE, policy.explain(rule, signers))

        # 7. MVCC: everything it read is still at the version it saw
        rw = result.get("rwset", {})
        for r in rw.get("reads", []):
            current = self._version(r["key"], overlay)
            seen_v = tuple(r["version"]) if r["version"] else None
            if current != seen_v:
                raise TxRejected(blk.MVCC_READ_CONFLICT,
                                 f"key {_printable(r['key'])} changed (read at {seen_v}, now {current})")

        # 8. phantom reads: ranges still return the same keys at the same versions
        for q in rw.get("ranges", []):
            now = self._range(q["start"], q["end"], overlay)
            then = [[x[0], list(x[1])] for x in q["results"]]
            if now != then:
                raise TxRejected(blk.PHANTOM_READ_CONFLICT,
                                 f"range {_printable(q['start'])}.. now returns different keys")
        return prop, result, creator

    def _version(self, key: str, overlay: dict[str, tuple[str | None, Version]]) -> Version | None:
        if key in overlay:
            value, version = overlay[key]
            return version if value is not None else None
        return self.ledger.get_version(key)

    def _range(self, start: str, end: str, overlay: dict[str, tuple[str | None, Version]]) -> list[list[Any]]:
        merged: dict[str, Version | None] = {k: v for k, _val, v in self.ledger.range_scan(start, end)}
        for key, (value, version) in overlay.items():
            if start <= key < end:
                merged[key] = version if value is not None else None
        return [[k, list(v)] for k, v in sorted(merged.items(), key=lambda kv: kv[0].encode()) if v is not None]

    # -- config transactions ----------------------------------------------------

    def validate_config_block(self, block: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        """A config block carries the update and the resulting config. Re-derive and compare."""
        data = block["data"]
        env = data.get("update")
        if env is None:
            return False, "config block without an update", None
        update = env.get("update", {})
        if update.get("sequence") != self.config.get("sequence", 0) + 1:
            return False, "config sequence out of order", None
        signers = []
        for s in env.get("signatures", []):
            try:
                ident = self.identities.get(s["signer"], None)
            except (MSPError, KeyError):
                continue
            if ident.verify(canonical(update), s.get("signature", "")):
                signers.append(ident)
        if not policy.majority_of(list(self.config["orgs"]), signers, role="admin"):
            return False, "config update lacks a MAJORITY of org admin signatures", None
        expected = canonical_copy(self.config)
        expected["sequence"] = update["sequence"]
        if update.get("kind") == "chaincode_definition":
            expected.setdefault("chaincodes", {})[update["chaincode"]] = update["definition"]
        if canonical(expected) != canonical(data.get("config")):
            return False, "config in block does not match the signed update", None
        return True, "", expected


def canonical_copy(obj: Any) -> Any:
    import json
    return json.loads(json.dumps(obj))


def _printable(key: str) -> str:
    return key.replace("\x00", "/").strip("/")
