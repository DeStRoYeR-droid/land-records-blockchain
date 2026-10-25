"""
Endorser: the EXECUTE step.

Given a signed proposal ("call RegisterParcel with these args, signed by
registrar1"), the endorser

  1. checks the proposal: right channel, the creator's certificate belongs to
     a channel member, the signature verifies, the txId really is
     SHA-256(nonce || creator), and the txId hasn't been used before;
  2. checks it runs the exact chaincode the channel agreed on (packageHash);
  3. simulates the function against its current world state;
  4. signs the RESULT: {txId, proposal hash, chaincode, response, rwset, event}.

The peer's signature says "I ran it and this is what I got". It writes
nothing. Every department's peer does this independently; the client then
checks that all results are identical before sending them to be ordered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..common import block as blk
from ..common import crypto
from ..common.canonical import canonical, hash_obj
from ..common.msp import MSPError
from ..common.web import NodeError
from ..shim import ChaincodeStub, ClientIdentity, Context
from .simulator import TxSimulator

if TYPE_CHECKING:
    from .server import Peer


class Endorser:
    def __init__(self, peer: "Peer"):
        self.peer = peer

    def _check(self, signed: dict[str, Any], query: bool):
        peer = self.peer
        if peer.channel_config is None or peer.msps is None:
            raise NodeError(503, "peer has not joined a channel")
        try:
            prop = signed["proposal"]
            sig = signed["signature"]
            fn, args, creator_wire = prop["fn"], prop["args"], prop["creator"]
            txid, nonce, ts = prop["txId"], prop["nonce"], prop["timestamp"]
            cc_name = prop["chaincode"]
        except (KeyError, TypeError) as exc:
            raise NodeError(400, f"malformed proposal: missing {exc}") from exc
        if prop.get("channel") != peer.channel_config["channel"]:
            raise NodeError(404, f"channel '{prop.get('channel')}' is not joined by this peer")
        try:
            creator = peer.msps.deserialize(creator_wire, at=crypto.parse_iso(ts))
        except (MSPError, ValueError) as exc:
            raise NodeError(403, f"creator rejected: {exc}") from exc
        if creator.msp_id not in peer.channel_config["orgs"]:
            raise NodeError(403, f"{creator.msp_id} is not an application org on this channel")
        if not creator.verify(canonical(prop), sig):
            raise NodeError(403, "proposal signature does not verify")
        if blk.compute_tx_id(nonce, creator_wire) != txid:
            raise NodeError(400, "txId is not SHA-256(nonce || creator)")
        allowed = ("client", "admin", "peer") if query else ("client", "admin")
        if creator.ou not in allowed:
            raise NodeError(403, f"{creator.ou} identities may not {'query' if query else 'submit'} on this channel")
        if not query and peer.ledger.has_tx(txid):
            raise NodeError(409, f"DUPLICATE_TXID: {txid[:16]}... is already on the ledger")
        definition = peer.channel_config.get("chaincodes", {}).get(cc_name)
        installed = peer.chaincodes.get(cc_name)
        if definition is None:
            raise NodeError(404, f"chaincode '{cc_name}' is not defined on this channel")
        if installed is None:
            raise NodeError(500, f"chaincode '{cc_name}' is not installed on {peer.name}")
        if installed.package_hash != definition["packageHash"]:
            raise NodeError(
                500, f"chaincode '{cc_name}' installed on {peer.name} (package {installed.package_hash[:12]}...) "
                     f"is not the committed definition (package {definition['packageHash'][:12]}..., "
                     f"sequence {definition['sequence']}). Commit the new code with scripts/upgrade_chaincode.py")
        return prop, creator, fn, args, installed, definition

    def endorse(self, signed: dict[str, Any]) -> dict[str, Any]:
        prop, creator, fn, args, installed, definition = self._check(signed, query=False)
        sim = TxSimulator(self.peer.ledger)
        ctx = Context(ChaincodeStub(sim, prop), ClientIdentity(creator))
        try:
            payload = installed.invoke(ctx, fn, args)
        except Exception as exc:  # the chaincode refused (e.g. ACCESS DENIED) or crashed
            self.peer.stats["endorsementsRefused"] += 1
            return {"status": 500, "message": str(exc), "peer": self.peer.name}
        result = {
            "txId": prop["txId"],
            "proposalHash": hash_obj(prop),
            "chaincode": {"name": installed.name, "version": definition["version"],
                          "sequence": definition["sequence"], "packageHash": installed.package_hash},
            "response": {"status": 200, "payload": payload},
            "rwset": sim.rwset(),
            "event": sim.event,
        }
        self.peer.stats["endorsements"] += 1
        return {
            "status": 200, "peer": self.peer.name, "result": result,
            "endorsement": {"endorser": self.peer.identity.wire(),
                            "signature": self.peer.identity.sign(canonical(result))},
        }

    def query(self, signed: dict[str, Any]) -> dict[str, Any]:
        prop, creator, fn, args, installed, _definition = self._check(signed, query=True)
        sim = TxSimulator(self.peer.ledger)
        ctx = Context(ChaincodeStub(sim, prop), ClientIdentity(creator))
        try:
            payload = installed.invoke(ctx, fn, args)
        except Exception as exc:
            return {"status": 500, "message": str(exc), "peer": self.peer.name}
        self.peer.stats["queries"] += 1
        return {"status": 200, "payload": payload, "peer": self.peer.name}
