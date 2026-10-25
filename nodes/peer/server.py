"""
Peer node: endorse proposals, pull blocks from the orderers, validate, commit.

Endpoints
    POST /admin/join        join the channel with its genesis block (org admin signed)
    POST /endorse           simulate a signed proposal and sign the result
    POST /query             simulate a signed proposal and return the payload (nothing is signed or ordered)
    GET  /txs/{txId}        validation result of a transaction (long-polls with ?wait=)
    GET  /events            Server-Sent Events: one message per committed block, with
                            each tx's validation code and chaincode event (?start=N replays)
    GET  /blocks            newest blocks, summarised      GET /blocks/{n}  one block
    GET  /state             world-state keys and versions (for exploring)
    GET  /channel/config    the channel configuration this peer is running with
    GET  /status            height, chaincode, counters (used by the dashboards)
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import random
import time
from typing import Any, AsyncIterator

from fastapi import Body

from ..common import block as blk
from ..common import config, crypto, log
from ..common.canonical import canonical
from ..common.msp import MSPError, MSPManager, SigningIdentity
from ..common.web import NodeError, client, create_app, sse_response
from .chaincode import InstalledChaincode
from .endorser import Endorser
from .ledger import Ledger
from .validator import BlockValidator, IdentityCache

logger = log.get("peer")


class Peer:
    def __init__(self, name: str):
        self.name = name
        self.msp_id = config.org_of(name)
        self.identity = SigningIdentity.from_msp_dir(config.node_msp_dir(name), self.msp_id)
        self.ledger = Ledger(config.data_dir(name) / "ledger.db")
        self.channel_config: dict[str, Any] | None = None
        self.msps: MSPManager | None = None
        self.identities: IdentityCache | None = None
        self.chaincodes: dict[str, InstalledChaincode] = {}
        self.endorser = Endorser(self)
        self.http = client(timeout=30.0)
        self.subscribers: set[asyncio.Queue] = set()
        self.committed = asyncio.Condition()
        self.deliver_task: asyncio.Task | None = None
        self.deliver_source: str | None = None
        self.last_commit_at: float | None = None
        self.recent_invalid: collections.deque = collections.deque(maxlen=20)
        self.stats = collections.Counter(endorsements=0, endorsementsRefused=0, queries=0,
                                         blocksCommitted=0, txValid=0, txInvalid=0)

    # -- config and chaincode -------------------------------------------------

    def _load_config(self, cfg: dict[str, Any]) -> None:
        self.channel_config = cfg
        self.msps = MSPManager.from_channel_config(cfg)
        self.identities = IdentityCache(self.msps)
        for name, definition in cfg.get("chaincodes", {}).items():
            current = self.chaincodes.get(name)
            if current and current.package_hash == definition["packageHash"]:
                continue
            try:
                cc = InstalledChaincode(name, definition["module"])
            except Exception as exc:
                logger.error("could not load chaincode %s from %s: %s", name, definition["module"], exc)
                continue
            self.chaincodes[name] = cc
            if cc.package_hash == definition["packageHash"]:
                logger.info("chaincode %s %s (sequence %s) loaded: %d functions, package %s...", name,
                            definition["version"], definition["sequence"], len(cc.functions), cc.package_hash[:12])
            else:
                logger.warning("chaincode %s on disk (package %s...) differs from the committed definition "
                               "(%s...): this peer will refuse to endorse until scripts/upgrade_chaincode.py is run",
                               name, cc.package_hash[:12], definition["packageHash"][:12])

    async def start(self) -> None:
        if self.ledger.height() == 0:
            logger.info("not joined to a channel yet; waiting for POST /admin/join")
            return
        import json
        cfg_text = self.ledger.get_meta("channel_config")
        if cfg_text is None:
            raise RuntimeError("ledger has blocks but no channel config")
        self._load_config(json.loads(cfg_text))
        logger.info("resuming channel %s at height %d", self.channel_config["channel"], self.ledger.height())
        self.deliver_task = asyncio.create_task(self._deliver_loop())

    async def stop(self) -> None:
        if self.deliver_task:
            self.deliver_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.deliver_task
        await self.http.aclose()

    # -- join -----------------------------------------------------------------

    def join(self, genesis: dict[str, Any], signer: dict[str, str], signature: str) -> dict[str, Any]:
        if self.ledger.height() > 0:
            if self.ledger.block_hash(0) == blk.block_hash(genesis):
                return {"joined": True, "already": True}
            raise NodeError(409, "this peer already joined a channel with a different genesis block")
        if not blk.is_config_block(genesis):
            raise NodeError(400, "not a genesis (config) block")
        err = blk.check_block_integrity(genesis, 0, blk.GENESIS_PREVIOUS_HASH)
        if err:
            raise NodeError(400, err)
        cfg = genesis["data"]["config"]
        my_root = (config.node_msp_dir(self.name) / "cacerts" / "ca.pem").read_text()
        if self.msp_id not in cfg["orgs"] or crypto.cert_to_pem(crypto.load_cert(my_root)) not in cfg["orgs"][self.msp_id]["rootCerts"]:
            raise NodeError(403, f"genesis block does not list {self.msp_id} with this peer's CA as its root")
        try:
            admin = MSPManager.from_channel_config(cfg).deserialize(signer)
        except MSPError as exc:
            raise NodeError(403, f"join signer rejected: {exc}") from exc
        if admin.msp_id != self.msp_id or admin.ou != "admin":
            raise NodeError(403, f"only a {self.msp_id} admin may join this peer to a channel")
        if not admin.verify(canonical({"action": "join", "blockHash": blk.block_hash(genesis)}), signature):
            raise NodeError(403, "join signature does not verify")
        genesis.setdefault("metadata", {})["validation"] = [blk.VALID]
        self.ledger.commit_block(genesis, blk.block_hash(genesis), [], [], [], config=cfg)
        self._load_config(cfg)
        logger.info("joined channel %s (genesis %s...)", cfg["channel"], blk.block_hash(genesis)[:12])
        self.deliver_task = asyncio.get_running_loop().create_task(self._deliver_loop())
        return {"joined": True, "already": False}

    # -- deliver: pull blocks from the ordering service ---------------------------

    async def _deliver_loop(self) -> None:
        assert self.channel_config is not None
        consenters = [(c["id"], f"http://{c['host']}:{c['port']}") for c in self.channel_config["orderer"]["consenters"]]
        i = random.randrange(len(consenters))
        failures = 0
        while True:
            name, url = consenters[i % len(consenters)]
            try:
                resp = await self.http.get(f"{url}/deliver", params={"start": self.ledger.height(), "wait": 20})
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                if self.deliver_source != name:
                    logger.info("receiving blocks from %s", name)
                    self.deliver_source = name
                failures = 0
                for b in resp.json()["blocks"]:
                    if b["header"]["number"] == self.ledger.height():
                        await self._commit(b)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                if failures == 1 or failures % 10 == 0:
                    logger.warning("deliver from %s failed (%s); trying the next orderer", name, exc)
                self.deliver_source = None
                i += 1
                await asyncio.sleep(min(0.5 * failures, 3.0))

    def _check_orderer_signature(self, b: dict[str, Any]) -> None:
        assert self.identities is not None and self.channel_config is not None
        sig = b.get("metadata", {}).get("signature")
        if not sig:
            raise RuntimeError("block is not signed by an orderer")
        ident = self.identities.get(sig["signer"], None)
        consenter_names = {crypto.cert_common_name(crypto.load_cert(c["cert"]))
                           for c in self.channel_config["orderer"]["consenters"]}
        if ident.msp_id != self.channel_config["orderer"]["org"]["mspId"] or ident.ou != "orderer" \
                or ident.name not in consenter_names:
            raise RuntimeError(f"block signer {ident.name} is not a consenter")
        if not ident.verify(blk.header_bytes(b), sig["signature"]):
            raise RuntimeError("orderer signature on block header is invalid")

    async def _commit(self, b: dict[str, Any]) -> None:
        assert self.channel_config is not None and self.identities is not None
        number = b["header"]["number"]
        prev_hash = self.ledger.block_hash(number - 1) or ""
        err = blk.check_block_integrity(b, number, prev_hash)
        if err:
            raise RuntimeError(err)
        try:
            self._check_orderer_signature(b)
        except MSPError as exc:
            raise RuntimeError(f"block signer rejected: {exc}") from exc

        validator = BlockValidator(self.ledger, self.channel_config, self.identities)
        bhash = blk.block_hash(b)
        if blk.is_config_block(b):
            ok, detail, new_cfg = validator.validate_config_block(b)
            code = blk.VALID if ok else blk.INVALID_CONFIG_TRANSACTION
            b["metadata"]["validation"] = [code]
            b["metadata"]["validationDetails"] = [detail]
            update_txid = b["data"].get("update", {}).get("update", {}).get("txId")
            self.ledger.commit_block(b, bhash, [code], [update_txid], [], config=new_cfg if ok else None)
            if ok and new_cfg:
                logger.info("block %d: CONFIG update to sequence %s applied", number, new_cfg["sequence"])
                self._load_config(new_cfg)
            else:
                logger.warning("block %d: config update rejected: %s", number, detail)
            self.stats["txValid" if ok else "txInvalid"] += 1
        else:
            res = validator.validate_transactions(b)
            b["metadata"]["validation"] = res["codes"]
            b["metadata"]["validationDetails"] = res["details"]
            self.ledger.commit_block(b, bhash, res["codes"], res["txIds"], res["updates"])
            valid = sum(1 for c in res["codes"] if c == blk.VALID)
            self.stats["txValid"] += valid
            self.stats["txInvalid"] += len(res["codes"]) - valid
            for tx in res["txs"]:
                if tx["code"] != blk.VALID:
                    self.recent_invalid.append({"block": number, **{k: tx.get(k) for k in ("txId", "fn", "code", "detail")}})
                    logger.info("block %d tx %d %s -> %s: %s", number, tx["index"], tx.get("fn"), tx["code"], tx["detail"])
            logger.info("committed block %d: %d/%d valid", number, valid, len(res["codes"]))
        self.stats["blocksCommitted"] += 1
        self.last_commit_at = time.time()
        event = self.block_event(b)
        for q in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)
        async with self.committed:
            self.committed.notify_all()

    # -- events ---------------------------------------------------------------

    def block_event(self, b: dict[str, Any]) -> dict[str, Any]:
        meta = b.get("metadata", {})
        codes = meta.get("validation", [])
        details = meta.get("validationDetails", [])
        txs = []
        if blk.is_config_block(b):
            update = b["data"].get("update", {}).get("update", {})
            txs.append({"txId": update.get("txId"), "index": 0, "fn": "ConfigUpdate",
                        "code": codes[0] if codes else None, "detail": details[0] if details else "",
                        "timestamp": update.get("timestamp"), "event": None,
                        "creator": _creator_summary(update.get("creator"))})
        for i, env in enumerate(b["data"].get("transactions", [])):
            p = env.get("signedProposal", {}).get("proposal", {})
            code = codes[i] if i < len(codes) else None
            txs.append({
                "txId": p.get("txId"), "index": i, "fn": p.get("fn"), "timestamp": p.get("timestamp"),
                "code": code, "detail": details[i] if i < len(details) else "",
                "creator": _creator_summary(p.get("creator")),
                "event": env.get("result", {}).get("event") if code == blk.VALID else None,
            })
        sig = meta.get("signature", {})
        return {"type": "block", "number": b["header"]["number"], "hash": blk.block_hash(b),
                "previousHash": b["header"]["previousHash"], "kind": b["data"].get("kind"),
                "orderer": sig.get("orderer"), "raft": meta.get("raft"), "txs": txs}

    async def events(self, start: int) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self.subscribers.add(q)
        try:
            nxt = self.ledger.height() if start < 0 else start
            while nxt < self.ledger.height():
                yield self.block_event(self.ledger.get_block(nxt))  # type: ignore[arg-type]
                nxt += 1
            while True:
                ev = await q.get()
                if ev["number"] < nxt:
                    continue
                while nxt < ev["number"]:  # we fell behind (queue overflow): fill from disk
                    yield self.block_event(self.ledger.get_block(nxt))  # type: ignore[arg-type]
                    nxt += 1
                yield ev
                nxt = ev["number"] + 1
        finally:
            self.subscribers.discard(q)

    # -- status ---------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        h = self.ledger.height()
        ccs = {}
        if self.channel_config:
            for name, d in self.channel_config.get("chaincodes", {}).items():
                inst = self.chaincodes.get(name)
                ccs[name] = {"version": d["version"], "sequence": d["sequence"],
                             "definedPackage": d["packageHash"],
                             "installedPackage": inst.package_hash if inst else None,
                             "matches": bool(inst and inst.package_hash == d["packageHash"]),
                             "policy": d["endorsementPolicy"],
                             "functions": inst.functions if inst else []}
        return {
            "name": self.name, "kind": "peer", "mspId": self.msp_id,
            "channel": self.channel_config["channel"] if self.channel_config else None,
            "joined": h > 0, "height": h, "lastBlockHash": self.ledger.block_hash(h - 1) if h else None,
            "configSequence": self.channel_config.get("sequence") if self.channel_config else None,
            "deliverSource": self.deliver_source,
            "lastCommitAgoS": round(time.time() - self.last_commit_at, 1) if self.last_commit_at else None,
            "worldStateKeys": self.ledger.count_keys(), "txCodes": self.ledger.count_txs(),
            "chaincodes": ccs, "stats": dict(self.stats), "recentInvalid": list(self.recent_invalid),
            "eventSubscribers": len(self.subscribers),
        }


_CN_CACHE: dict[str, str] = {}


def _creator_summary(wire: dict[str, str] | None) -> dict[str, Any] | None:
    if not wire:
        return None
    pem = wire.get("cert", "")
    if pem not in _CN_CACHE:
        try:
            cert = crypto.load_cert(pem)
            attrs = crypto.cert_attributes(cert)
            _CN_CACHE[pem] = f"{crypto.cert_common_name(cert)}|{attrs.get('role', '')}|{crypto.cert_node_ou(cert) or ''}"
        except Exception:
            _CN_CACHE[pem] = "?||"
        if len(_CN_CACHE) > 500:
            _CN_CACHE.clear()
    name, role, ou = (_CN_CACHE.get(pem) or "?||").split("|")
    return {"mspId": wire.get("mspId"), "name": name, "role": role or None, "ou": ou or None}


def build_app(name: str):
    peer = Peer(name)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        await peer.start()
        yield
        await peer.stop()

    app = create_app(f"LandChain peer · {name}",
                     f"Endorsing and committing peer for {peer.msp_id}.", lifespan=lifespan)
    app.state.peer = peer

    @app.get("/health")
    async def health():
        return {"status": "ok", "name": name, "height": peer.ledger.height()}

    @app.get("/status")
    async def status():
        return peer.status()

    @app.post("/admin/join")
    async def join(body: dict = Body(...)):
        return peer.join(body["block"], body["signer"], body["signature"])

    @app.post("/endorse")
    async def endorse(signed: dict = Body(...)):
        return peer.endorser.endorse(signed)

    @app.post("/query")
    async def query(signed: dict = Body(...)):
        return peer.endorser.query(signed)

    @app.get("/txs/{tx_id}")
    async def tx_status(tx_id: str, wait: float = 0.0):
        deadline = time.monotonic() + max(0.0, min(wait, 55.0))
        async with peer.committed:
            while True:
                st = peer.ledger.tx_status(tx_id)
                remaining = deadline - time.monotonic()
                if st or remaining <= 0:
                    break
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(peer.committed.wait(), remaining)
        if not st:
            raise NodeError(404, f"transaction {tx_id[:16]}... not committed on {name} (yet)")
        return st

    @app.get("/events")
    async def events(start: int = -1):
        return sse_response(peer.events(start))

    @app.get("/blocks")
    async def blocks(before: int | None = None, limit: int = 20):
        h = peer.ledger.height()
        out = []
        for b in peer.ledger.blocks_desc(h if before is None else before, max(1, min(limit, 100))):
            ev = peer.block_event(b)
            out.append({k: ev[k] for k in ("number", "hash", "previousHash", "kind", "orderer", "raft")}
                       | {"txCount": len(ev["txs"]), "validCount": sum(1 for t in ev["txs"] if t["code"] == blk.VALID),
                          "timestamp": next((t["timestamp"] for t in ev["txs"] if t.get("timestamp")), None),
                          "txs": ev["txs"]})
        return {"height": h, "blocks": out}

    @app.get("/blocks/{number}")
    async def get_block(number: int):
        b = peer.ledger.get_block(number)
        if b is None:
            raise NodeError(404, f"block {number} not found (height {peer.ledger.height()})")
        return b

    @app.get("/state")
    async def state(prefix: str = "", limit: int = 200):
        prefix = prefix.replace("/", "\x00") if prefix.startswith("/") else prefix
        return {"keys": peer.ledger.list_state(prefix, max(1, min(limit, 1000)))}

    @app.get("/channel/config")
    async def channel_config():
        if not peer.channel_config:
            raise NodeError(404, "not joined")
        return peer.channel_config

    return app
