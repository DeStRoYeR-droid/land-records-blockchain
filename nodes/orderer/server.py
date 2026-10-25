"""
Orderer node: Raft + block cutter + broadcast/deliver APIs.

Request path of a transaction:

    client --POST /broadcast--> any orderer
        follower: forwards to the current leader (Fabric does the same)
        leader:   checks the envelope (signature, channel Writers policy),
                  queues it in the block cutter
    block cutter: when 10 txs are queued or 2 s passed since the first one,
                  the leader builds block N (previousHash = hash of block N-1),
                  signs its header and proposes it as a Raft log entry
    Raft:         once 2 of 3 orderers stored the entry it is committed;
                  every orderer then appends the block to its block store
    peers --GET /deliver?start=N--> receive committed blocks in order

Endpoints
    POST /admin/join     join the channel with its genesis block (org admin signed)
    POST /broadcast      submit a transaction envelope
    GET  /deliver        long-poll for committed blocks from a given number
    GET  /blocks/{n}     one block
    GET  /status         Raft role, term, leader, heights (used by the dashboards)
    POST /raft/{rpc}     Raft RPCs between orderers (vote, append), signed
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import time
from typing import Any

from fastapi import Body, Header, Request

from ..common import block as blk
from ..common import config, crypto, log, policy
from ..common.canonical import canonical, hash_obj
from ..common.msp import MSPError, MSPManager, SigningIdentity
from ..common.web import NodeError, client, create_app, error_message
from .raft import LEADER, NotLeader, RaftNode, RaftStorage

logger = log.get("orderer")
raft_log = log.get("raft")

MAX_ENVELOPE_BYTES = 2 * 1024 * 1024


class Orderer:
    def __init__(self, name: str):
        self.name = name
        self.identity = SigningIdentity.from_msp_dir(config.node_msp_dir(name), "OrdererMSP")
        self.db = sqlite3.connect(config.data_dir(name) / "orderer.db", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS blocks (number INTEGER PRIMARY KEY, hash TEXT, data TEXT)")
        self.db.commit()
        self.raft_storage = RaftStorage(self.db)
        self.raft: RaftNode | None = None
        self.channel_config: dict[str, Any] | None = None
        self.msps: MSPManager | None = None
        self.consenter_certs: dict[str, Any] = {}
        self.consenter_urls: dict[str, str] = {}
        self.http = client(timeout=5.0)
        self.raft_http = client(timeout=1.0)
        self.new_block = asyncio.Condition()
        # block cutter state (leader only)
        self.pending: list[dict[str, Any]] = []
        self.pending_bytes = 0
        self.pending_since = 0.0
        self.cutter_task: asyncio.Task | None = None
        # the newest block in our Raft log (committed or not): where the next block chains on
        self.tip_number = -1
        self.tip_hash = ""
        self.stats = {"broadcastAccepted": 0, "forwarded": 0, "blocksCut": 0}

    # -- block store ---------------------------------------------------------

    def height(self) -> int:
        row = self.db.execute("SELECT MAX(number) FROM blocks").fetchone()
        return (row[0] + 1) if row and row[0] is not None else 0

    def get_block(self, number: int) -> dict[str, Any] | None:
        row = self.db.execute("SELECT data FROM blocks WHERE number=?", (number,)).fetchone()
        return json.loads(row[0]) if row else None

    def blocks_from(self, start: int, limit: int) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT data FROM blocks WHERE number>=? ORDER BY number LIMIT ?",
                               (start, limit)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def _store_block(self, block: dict[str, Any], raft_index: int | None) -> None:
        self.db.execute("INSERT INTO blocks (number, hash, data) VALUES (?, ?, ?)",
                        (block["header"]["number"], blk.block_hash(block), json.dumps(block)))
        if raft_index is not None:
            self.raft_storage.set_meta("applied_index", str(raft_index), commit=False)
        self.db.commit()

    # -- channel config ------------------------------------------------------

    def _load_config(self, cfg: dict[str, Any]) -> None:
        self.channel_config = cfg
        self.msps = MSPManager.from_channel_config(cfg)
        self.consenter_certs = {c["id"]: crypto.load_cert(c["cert"]) for c in cfg["orderer"]["consenters"]}
        self.consenter_urls = {c["id"]: f"http://{c['host']}:{c['port']}" for c in cfg["orderer"]["consenters"]}

    def _latest_config(self) -> dict[str, Any] | None:
        rows = self.db.execute("SELECT data FROM blocks ORDER BY number DESC").fetchall()
        for (data,) in rows:
            b = json.loads(data)
            if blk.is_config_block(b):
                return b["data"]["config"]
        return None

    # -- join ----------------------------------------------------------------

    def join(self, genesis: dict[str, Any], signer: dict[str, str], signature: str) -> dict[str, Any]:
        if self.height() > 0:
            existing = self.get_block(0)
            if existing and blk.block_hash(existing) == blk.block_hash(genesis):
                return {"joined": True, "already": True}
            raise NodeError(409, "this orderer already joined a channel with a different genesis block")
        if genesis["header"]["number"] != 0 or not blk.is_config_block(genesis):
            raise NodeError(400, "not a genesis (config) block")
        err = blk.check_block_integrity(genesis, 0, blk.GENESIS_PREVIOUS_HASH)
        if err:
            raise NodeError(400, err)
        cfg = genesis["data"]["config"]
        msps = MSPManager.from_channel_config(cfg)
        # The genesis block must name OUR organisation's CA as the orderer org root,
        # otherwise anyone could make us join a channel with a fake root of trust.
        my_root = crypto.load_cert((config.node_msp_dir(self.name) / "cacerts" / "ca.pem").read_text())
        if crypto.cert_to_pem(my_root) not in cfg["orderer"]["org"]["rootCerts"]:
            raise NodeError(403, "genesis block does not trust this orderer's own CA")
        try:
            admin = msps.deserialize(signer)
        except MSPError as exc:
            raise NodeError(403, f"join signer rejected: {exc}") from exc
        if admin.msp_id != "OrdererMSP" or admin.ou != "admin":
            raise NodeError(403, "only an OrdererMSP admin may join this orderer to a channel")
        if not admin.verify(canonical({"action": "join", "blockHash": blk.block_hash(genesis)}), signature):
            raise NodeError(403, "join signature does not verify")
        if self.name not in [c["id"] for c in cfg["orderer"]["consenters"]]:
            raise NodeError(403, f"{self.name} is not in the channel's consenter set")
        self._store_block(genesis, None)
        logger.info("joined channel %s (genesis %s...)", cfg["channel"], blk.block_hash(genesis)[:12])
        return {"joined": True, "already": False}

    # -- raft wiring ---------------------------------------------------------

    async def start_raft(self) -> None:
        cfg = self._latest_config()
        if cfg is None:
            logger.info("not joined to any channel yet; waiting for POST /admin/join")
            return
        self._load_config(cfg)
        r = cfg["orderer"]["raft"]
        applied = int(self.raft_storage.meta("applied_index", "0"))
        self.raft = RaftNode(
            node_id=self.name, cluster=list(self.consenter_urls), storage=self.raft_storage,
            transport=self._raft_send, apply_fn=self._apply, applied_index=applied,
            heartbeat_ms=r["heartbeatMs"], election_timeout_ms=tuple(r["electionTimeoutMs"]),
            logger=raft_log, on_leader_change=self._on_leader_change)
        await self.raft.start()
        self.cutter_task = asyncio.create_task(self._cutter_loop())

    async def stop(self) -> None:
        if self.cutter_task:
            self.cutter_task.cancel()
        if self.raft:
            await self.raft.stop()
        await self.http.aclose()
        await self.raft_http.aclose()
        self.db.close()

    async def _raft_send(self, peer: str, rpc: str, msg: dict[str, Any]) -> dict[str, Any] | None:
        body = {"from": self.name, "rpc": rpc, "msg": msg}
        body["sig"] = self.identity.sign(canonical(body))
        try:
            resp = await self.raft_http.post(f"{self.consenter_urls[peer]}/raft/{rpc}", json=body)
            return resp.json() if resp.status_code == 200 else None
        except Exception:
            return None

    def handle_raft(self, rpc: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.raft is None:
            raise NodeError(503, "raft not started")
        sender = body.get("from")
        cert = self.consenter_certs.get(sender)
        if cert is None:
            raise NodeError(403, f"{sender} is not a consenter on this channel")
        sig = body.get("sig", "")
        unsigned = {k: v for k, v in body.items() if k != "sig"}
        if not crypto.verify(cert.public_key(), canonical(unsigned), sig):
            raise NodeError(403, "raft message signature invalid")
        if rpc == "vote":
            return self.raft.handle_vote(body["msg"])
        if rpc == "append":
            return self.raft.handle_append(body["msg"])
        raise NodeError(404, f"unknown raft rpc {rpc}")

    def _apply(self, raft_index: int, data: str) -> None:
        """Raft says entry `raft_index` is committed: write the block (if any)."""
        if not data:  # the empty entry a new leader appends
            self.raft_storage.set_meta("applied_index", str(raft_index))
            return
        block = json.loads(data)
        number = block["header"]["number"]
        height = self.height()
        if number < height:  # already stored (re-applied after a restart)
            self.raft_storage.set_meta("applied_index", str(raft_index))
            return
        if number != height:
            logger.error("committed block %s but store height is %s", number, height)
            return
        prev = self.get_block(number - 1)
        err = blk.check_block_integrity(block, number, blk.block_hash(prev) if prev else "")
        if err:
            logger.error("refusing committed block: %s", err)
            return
        self._store_block(block, raft_index)
        if blk.is_config_block(block):
            self._load_config(block["data"]["config"])
            logger.info("applied CONFIG block %s (sequence %s)", number, block["data"]["config"].get("sequence"))
        else:
            logger.info("committed block %s with %d tx (raft index %s, term %s)", number,
                        len(block["data"]["transactions"]), raft_index, block["metadata"]["raft"]["term"])
        asyncio.get_running_loop().create_task(self._notify_block())

    async def _notify_block(self) -> None:
        async with self.new_block:
            self.new_block.notify_all()

    def _on_leader_change(self, leader: str | None) -> None:
        if leader == self.name:
            self._refresh_tip()
            return
        if self.pending:
            # Hand queued-but-uncut transactions to the new leader instead of dropping them.
            stranded, self.pending, self.pending_bytes = self.pending, [], 0
            if leader:
                asyncio.get_running_loop().create_task(self._forward_many(stranded, leader))
            else:
                logger.warning("dropping %d queued tx: no leader known (clients will retry)", len(stranded))

    async def _forward_many(self, envelopes: list[dict[str, Any]], leader: str) -> None:
        for env in envelopes:
            with contextlib.suppress(Exception):
                await self.http.post(f"{self.consenter_urls[leader]}/broadcast", json=env,
                                     headers={"X-Forwarded-By": self.name})
        logger.info("forwarded %d queued tx to new leader %s", len(envelopes), leader)

    def _refresh_tip(self) -> None:
        """Find the newest block in our log (it may not be committed yet)."""
        assert self.raft is not None
        idx = self.raft_storage.last_index()
        while idx > 0:
            data = self.raft_storage.data_at(idx)
            if data:
                b = json.loads(data)
                self.tip_number, self.tip_hash = b["header"]["number"], blk.block_hash(b)
                return
            idx -= 1
        last = self.get_block(self.height() - 1)
        assert last is not None
        self.tip_number, self.tip_hash = last["header"]["number"], blk.block_hash(last)

    # -- broadcast -----------------------------------------------------------

    def _check_envelope(self, env: dict[str, Any]) -> None:
        """Fabric's 'msgprocessor': is the submitter allowed to write to this channel?"""
        assert self.msps is not None and self.channel_config is not None
        if len(canonical(env)) > MAX_ENVELOPE_BYTES:
            raise NodeError(413, "envelope too large")
        creator = blk.envelope_creator(env)
        if creator is None or blk.envelope_tx_id(env) is None or "signature" not in env:
            raise NodeError(400, "malformed envelope")
        try:
            ident = self.msps.deserialize(creator, at=crypto.utcnow())
        except MSPError as exc:
            raise NodeError(403, f"BAD_CREATOR: {exc}") from exc
        if ident.msp_id not in self.channel_config["orgs"] or ident.ou not in ("client", "admin"):
            raise NodeError(403, f"{ident.name} ({ident.msp_id}.{ident.ou}) does not satisfy the channel Writers policy")
        if not ident.verify(blk.envelope_signed_part(env), env["signature"]):
            raise NodeError(403, "BAD_CREATOR_SIGNATURE: envelope signature does not verify")
        if env.get("type") == "config_update":
            self._check_config_update(env)
        elif env.get("type") != "endorser_transaction":
            raise NodeError(400, f"unsupported envelope type {env.get('type')}")

    def _check_config_update(self, env: dict[str, Any]) -> None:
        assert self.msps is not None and self.channel_config is not None
        update = env["update"]
        if update.get("sequence") != self.channel_config.get("sequence", 0) + 1:
            raise NodeError(409, f"config sequence must be {self.channel_config.get('sequence', 0) + 1}")
        signers = []
        for s in env.get("signatures", []):
            try:
                ident = self.msps.deserialize(s["signer"])
            except MSPError:
                continue
            if ident.verify(canonical(update), s["signature"]):
                signers.append(ident)
        orgs = list(self.channel_config["orgs"])
        if not policy.majority_of(orgs, signers, role="admin"):
            raise NodeError(403, "config update needs signatures from a MAJORITY of org admins")

    async def broadcast(self, env: dict[str, Any], forwarded_by: str | None) -> dict[str, Any]:
        if self.raft is None:
            raise NodeError(503, "SERVICE_UNAVAILABLE: orderer not joined to a channel")
        if self.raft.state != LEADER:
            leader = self.raft.leader_id
            if forwarded_by or not leader:
                raise NodeError(503, "SERVICE_UNAVAILABLE: no Raft leader right now (election in progress)",
                                leader=leader)
            try:
                resp = await self.http.post(f"{self.consenter_urls[leader]}/broadcast", json=env,
                                            headers={"X-Forwarded-By": self.name})
            except Exception as exc:
                raise NodeError(503, f"SERVICE_UNAVAILABLE: leader {leader} unreachable ({type(exc).__name__})",
                                leader=leader) from exc
            if resp.status_code != 200:
                raise NodeError(resp.status_code, error_message(resp))
            self.stats["forwarded"] += 1
            return {**resp.json(), "forwardedBy": self.name}

        self._check_envelope(env)
        if env.get("type") == "config_update":
            self._cut()  # a config transaction always gets a block of its own
            self._cut_config(env)
        else:
            if not self.pending:
                self.pending_since = time.monotonic()
            self.pending.append(env)
            self.pending_bytes += len(canonical(env))
            batch = self.channel_config["orderer"]["batch"]  # type: ignore[index]
            if len(self.pending) >= batch["maxMessageCount"] or self.pending_bytes >= batch["preferredMaxBytes"]:
                self._cut()
        self.stats["broadcastAccepted"] += 1
        return {"status": "SUCCESS", "txId": blk.envelope_tx_id(env), "leader": self.name}

    async def _cutter_loop(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            if not self.raft or self.raft.state != LEADER or not self.pending:
                continue
            timeout = self.channel_config["orderer"]["batch"]["timeoutMs"] / 1000  # type: ignore[index]
            if time.monotonic() - self.pending_since >= timeout:
                self._cut()

    def _next_block(self, data: dict[str, Any]) -> dict[str, Any]:
        assert self.raft is not None
        number = self.tip_number + 1
        raft_index = self.raft_storage.last_index() + 1  # where propose() will put it
        block = blk.make_block(number, self.tip_hash, data,
                               {"raft": {"term": self.raft.term, "index": raft_index}})
        block["metadata"]["signature"] = {
            "signer": self.identity.wire(), "orderer": self.name,
            "signature": self.identity.sign(blk.header_bytes(block)),
        }
        return block

    def _propose_block(self, block: dict[str, Any]) -> None:
        assert self.raft is not None
        try:
            self.raft.propose(json.dumps(block))
        except NotLeader:
            logger.warning("lost leadership while cutting block %s", block["header"]["number"])
            return
        self.tip_number, self.tip_hash = block["header"]["number"], blk.block_hash(block)
        self.stats["blocksCut"] += 1

    def _cut(self) -> None:
        if not self.pending or not self.raft or self.raft.state != LEADER:
            return
        batch, self.pending, self.pending_bytes = self.pending, [], 0
        block = self._next_block({"kind": "transactions", "transactions": batch})
        logger.info("cut block %s with %d tx", block["header"]["number"], len(batch))
        self._propose_block(block)

    def _cut_config(self, env: dict[str, Any]) -> None:
        assert self.channel_config is not None
        new_config = json.loads(json.dumps(self.channel_config))
        update = env["update"]
        new_config["sequence"] = update["sequence"]
        if update["kind"] == "chaincode_definition":
            new_config["chaincodes"][update["chaincode"]] = update["definition"]
        else:
            raise NodeError(400, f"unsupported config update kind {update['kind']}")
        block = self._next_block({"kind": "config", "config": new_config, "update": env})
        logger.info("cut CONFIG block %s (sequence %s)", block["header"]["number"], update["sequence"])
        self._propose_block(block)

    # -- status --------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        h = self.height()
        last = self.get_block(h - 1) if h else None
        return {
            "name": self.name, "kind": "orderer", "mspId": "OrdererMSP",
            "channel": self.channel_config["channel"] if self.channel_config else None,
            "joined": h > 0, "height": h, "lastBlockHash": blk.block_hash(last) if last else None,
            "raft": self.raft.status() if self.raft else None,
            "pendingTx": len(self.pending), "stats": self.stats,
        }


def build_app(name: str):
    orderer = Orderer(name)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        await orderer.start_raft()
        yield
        await orderer.stop()

    app = create_app(f"LandChain orderer · {name}",
                     "Raft ordering node. Accepts signed envelopes, cuts blocks, delivers them to peers.",
                     lifespan=lifespan)
    app.state.orderer = orderer

    @app.get("/health")
    async def health():
        return {"status": "ok", "name": name, "joined": orderer.height() > 0}

    @app.get("/status")
    async def status():
        return orderer.status()

    @app.post("/admin/join")
    async def join(body: dict = Body(...)):
        result = orderer.join(body["block"], body["signer"], body["signature"])
        if not result.get("already") and orderer.raft is None:
            await orderer.start_raft()
        return result

    @app.post("/broadcast")
    async def broadcast(env: dict = Body(...), x_forwarded_by: str | None = Header(None)):
        return await orderer.broadcast(env, x_forwarded_by)

    @app.get("/deliver")
    async def deliver(start: int = 0, wait: float = 20.0, limit: int = 20):
        deadline = time.monotonic() + max(0.0, min(wait, 55.0))
        # Check-then-wait under the condition's lock, so a block committed in
        # between can't slip past unnoticed.
        async with orderer.new_block:
            while True:
                blocks = orderer.blocks_from(start, max(1, min(limit, 100)))
                remaining = deadline - time.monotonic()
                if blocks or remaining <= 0:
                    return {"blocks": blocks, "height": orderer.height()}
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(orderer.new_block.wait(), remaining)

    @app.get("/blocks/{number}")
    async def get_block(number: int):
        b = orderer.get_block(number)
        if b is None:
            raise NodeError(404, f"block {number} not found (height {orderer.height()})")
        return b

    @app.post("/raft/{rpc}")
    async def raft_rpc(rpc: str, request: Request):
        return orderer.handle_raft(rpc, await request.json())

    return app
