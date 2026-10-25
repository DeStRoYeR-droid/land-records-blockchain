"""Gateway: proposals, endorsement collection, ordering, commit wait."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ..common import block as blk
from ..common import config, crypto, log
from ..common.canonical import canonical
from ..common.msp import SigningIdentity
from ..common.web import error_message

logger = log.get("sdk")


class ChaincodeError(Exception):
    """The chaincode refused the call (e.g. 'ACCESS DENIED: ...'). Nothing was ordered."""

    def __init__(self, message: str, peer: str | None = None):
        super().__init__(message)
        self.message = message
        self.peer = peer


class EndorsementMismatch(Exception):
    """Peers returned different results for the same proposal."""


class NetworkUnavailable(Exception):
    """A peer or every orderer could not be reached."""


class CommitError(Exception):
    """The transaction was ordered but the peers marked it invalid."""

    def __init__(self, code: str, detail: str, tx_id: str, block: int | None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.tx_id = tx_id
        self.block = block


@dataclass
class SubmitResult:
    tx_id: str
    block: int
    code: str
    payload: str
    endorsers: list[str]

    def json(self) -> Any:
        return json.loads(self.payload) if self.payload else None


def build_signed_proposal(identity: SigningIdentity, channel: str, chaincode: str, fn: str,
                          args: list[str]) -> dict[str, Any]:
    creator = identity.wire()
    nonce = blk.new_nonce()
    proposal = {
        "channel": channel, "chaincode": chaincode, "fn": fn, "args": [str(a) for a in args],
        "creator": creator, "nonce": nonce, "txId": blk.compute_tx_id(nonce, creator),
        "timestamp": crypto.iso(crypto.utcnow()),
    }
    return {"proposal": proposal, "signature": identity.sign(canonical(proposal))}


class Gateway:
    def __init__(self, identity: SigningIdentity, endorsing_peers: dict[str, str], orderers: list[str],
                 home_peer: str, channel: str | None = None, http: httpx.AsyncClient | None = None):
        self.identity = identity
        self.endorsing_peers = endorsing_peers   # name -> url, one per org
        self.orderers = orderers
        self.home_peer = home_peer               # name of the peer we query and watch
        self.channel = channel or config.channel_name()
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0))
        self._own_http = http is None

    @classmethod
    def for_identity(cls, identity: SigningIdentity, http: httpx.AsyncClient | None = None) -> "Gateway":
        peers = {config.org_peer(m): config.url(config.org_peer(m)) for m in config.application_orgs()}
        orderers = [config.url(o) for o in config.nodes_of_kind("orderer")]
        home = config.org_peer(identity.msp_id) if identity.msp_id in config.application_orgs() \
            else next(iter(peers))
        return cls(identity, peers, orderers, home, http=http)

    @classmethod
    def for_user(cls, msp_id: str, user: str, http: httpx.AsyncClient | None = None) -> "Gateway":
        ident = SigningIdentity.from_msp_dir(config.user_msp_dir(msp_id, user), msp_id)
        return cls.for_identity(ident, http)

    async def close(self) -> None:
        if self._own_http:
            await self.http.aclose()

    # -- queries ------------------------------------------------------------

    async def evaluate(self, chaincode: str, fn: str, *args: Any, peer: str | None = None) -> str:
        signed = build_signed_proposal(self.identity, self.channel, chaincode, fn, list(args))
        target = peer or self.home_peer
        try:
            resp = await self.http.post(f"{self.endorsing_peers[target]}/query", json=signed)
        except httpx.HTTPError as exc:
            raise NetworkUnavailable(f"{target} unreachable ({type(exc).__name__})") from exc
        if resp.status_code != 200:
            raise ChaincodeError(error_message(resp), target)
        body = resp.json()
        if body.get("status") != 200:
            raise ChaincodeError(body.get("message", "query failed"), target)
        return body.get("payload", "")

    async def evaluate_json(self, chaincode: str, fn: str, *args: Any) -> Any:
        payload = await self.evaluate(chaincode, fn, *args)
        return json.loads(payload) if payload else None

    # -- transactions ---------------------------------------------------------

    async def _endorse_all(self, signed: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
        async def one(name: str, url: str):
            try:
                resp = await self.http.post(f"{url}/endorse", json=signed)
            except httpx.HTTPError as exc:
                raise NetworkUnavailable(f"peer {name} unreachable ({type(exc).__name__}); "
                                         "the endorsement policy needs every department's peer") from exc
            if resp.status_code != 200:
                raise ChaincodeError(error_message(resp), name)
            body = resp.json()
            if body.get("status") != 200:
                raise ChaincodeError(body.get("message", "endorsement refused"), name)
            return name, body

        results = await asyncio.gather(*(one(n, u) for n, u in self.endorsing_peers.items()))
        first = canonical(results[0][1]["result"])
        mismatched = [n for n, body in results if canonical(body["result"]) != first]
        if mismatched:
            raise EndorsementMismatch(f"read/write sets differ between {results[0][0]} and {', '.join(mismatched)}")
        return results[0][1]["result"], [body["endorsement"] for _, body in results], [n for n, _ in results]

    async def submit(self, chaincode: str, fn: str, *args: Any, commit_timeout: float = 45.0,
                     retry_conflicts: int = 2) -> SubmitResult:
        """Run a full transaction. On MVCC/phantom conflicts (someone changed what we
        read before our block), simulate again on fresh state -- the chaincode
        re-checks every precondition, so retrying can't do anything it wouldn't."""
        for attempt in range(retry_conflicts + 1):
            try:
                return await self._submit_once(chaincode, fn, list(args), commit_timeout)
            except CommitError as exc:
                if exc.code not in (blk.MVCC_READ_CONFLICT, blk.PHANTOM_READ_CONFLICT) or attempt == retry_conflicts:
                    raise
                logger.info("%s hit %s; re-simulating on fresh state (attempt %d)", fn, exc.code, attempt + 2)
                await asyncio.sleep(0.2 * (attempt + 1))
        raise AssertionError("unreachable")

    async def _submit_once(self, chaincode: str, fn: str, args: list[Any], commit_timeout: float) -> SubmitResult:
        signed = build_signed_proposal(self.identity, self.channel, chaincode, fn, list(args))
        tx_id = signed["proposal"]["txId"]

        # 1-3. endorse on every department's peer; retry if a peer was behind
        for attempt in range(1, 6):
            try:
                result, endorsements, endorsers = await self._endorse_all(signed)
                break
            except EndorsementMismatch as exc:
                if attempt == 5:
                    raise
                logger.info("endorsement mismatch (%s); a peer is probably a block behind, retrying", exc)
                await asyncio.sleep(0.4 * attempt)

        # 4. envelope -> ordering service
        envelope = {"type": "endorser_transaction", "signedProposal": signed, "result": result,
                    "endorsements": endorsements}
        envelope["signature"] = self.identity.sign(blk.envelope_signed_part(envelope))
        await self._broadcast(envelope)

        # 5. wait for commit; re-broadcast once if it seems lost (e.g. leader died mid-batch)
        deadline = time.monotonic() + commit_timeout
        rebroadcast_at = time.monotonic() + 12.0
        status = None
        while time.monotonic() < deadline:
            status = await self._tx_status(self.home_peer, tx_id, wait=min(5.0, max(0.5, deadline - time.monotonic())))
            if status:
                break
            if time.monotonic() > rebroadcast_at:
                logger.info("tx %s... not committed yet; re-broadcasting", tx_id[:12])
                await self._broadcast(envelope)
                rebroadcast_at = time.monotonic() + 15.0
        if not status:
            raise NetworkUnavailable(f"transaction {tx_id[:16]}... was not committed within {commit_timeout:.0f}s")
        if status["code"] != blk.VALID:
            detail = await self._tx_detail(self.home_peer, status)
            raise CommitError(status["code"], detail, tx_id, status["block"])
        # make the write visible on every department's peer before returning
        await asyncio.gather(*(self._tx_status(n, tx_id, wait=10.0) for n in self.endorsing_peers if n != self.home_peer))
        return SubmitResult(tx_id, status["block"], status["code"], result["response"]["payload"], endorsers)

    async def submit_json(self, chaincode: str, fn: str, *args: Any) -> Any:
        return (await self.submit(chaincode, fn, *args)).json()

    async def _broadcast(self, envelope: dict[str, Any], budget: float = 30.0) -> dict[str, Any]:
        deadline = time.monotonic() + budget
        last_error = "no orderer reachable"
        i = 0
        while time.monotonic() < deadline:
            url = self.orderers[i % len(self.orderers)]
            i += 1
            try:
                resp = await self.http.post(f"{url}/broadcast", json=envelope)
            except httpx.HTTPError as exc:
                last_error = f"{url} unreachable ({type(exc).__name__})"
                await asyncio.sleep(0.2)
                continue
            if resp.status_code == 200:
                return resp.json()
            last_error = error_message(resp)
            if resp.status_code == 503:  # election in progress: try again shortly
                await asyncio.sleep(0.5)
                continue
            raise NetworkUnavailable(f"orderer refused the transaction: {last_error}")
        raise NetworkUnavailable(f"ordering service unavailable: {last_error}")

    async def _tx_status(self, peer: str, tx_id: str, wait: float) -> dict[str, Any] | None:
        try:
            resp = await self.http.get(f"{self.endorsing_peers[peer]}/txs/{tx_id}", params={"wait": wait},
                                       timeout=wait + 5)
        except httpx.HTTPError:
            await asyncio.sleep(0.5)
            return None
        return resp.json() if resp.status_code == 200 else None

    async def _tx_detail(self, peer: str, status: dict[str, Any]) -> str:
        try:
            resp = await self.http.get(f"{self.endorsing_peers[peer]}/blocks/{status['block']}")
            details = resp.json().get("metadata", {}).get("validationDetails", [])
            return details[status["index"]] if status["index"] < len(details) else ""
        except Exception:
            return ""

    # -- config updates (chaincode upgrade) ------------------------------------

    async def submit_config_update(self, update_body: dict[str, Any], signers: list[SigningIdentity]) -> dict[str, Any]:
        creator = self.identity.wire()
        nonce = blk.new_nonce()
        update = {**update_body, "channel": self.channel, "creator": creator, "nonce": nonce,
                  "txId": blk.compute_tx_id(nonce, creator), "timestamp": crypto.iso(crypto.utcnow())}
        envelope = {"type": "config_update", "update": update,
                    "signatures": [{"signer": s.wire(), "signature": s.sign(canonical(update))} for s in signers]}
        envelope["signature"] = self.identity.sign(blk.envelope_signed_part(envelope))
        await self._broadcast(envelope)
        status = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not status:
            status = await self._tx_status(self.home_peer, update["txId"], wait=5.0)
        if not status:
            raise NetworkUnavailable("config update was not committed in time")
        if status["code"] != blk.VALID:
            raise CommitError(status["code"], await self._tx_detail(self.home_peer, status), update["txId"],
                              status["block"])
        return status
