"""
One-time network administration, called by landchain_ctl.py setup.

  issue_identities()  register + enroll every certificate with each org's CA
  build_genesis()     write channel-artifacts/landchannel.block
  join_channel()      orderers join (OrdererMSP admin signs), wait for a Raft
                      leader, then each peer joins (its own org admin signs)

Everything here is also runnable step by step from a Python shell, which is
the best way to watch the network being born.
"""

from __future__ import annotations

import json
import time
from typing import Callable

import httpx

from nodes.ca import client as ca_client
from nodes.common import block as blk
from nodes.common import config
from nodes.common.canonical import canonical
from nodes.common.msp import SigningIdentity
from nodes.common.web import error_message

Say = Callable[[str], None]


def issue_identities(say: Say = print) -> None:
    topo = config.topology()
    for msp_id, org in topo["organizations"].items():
        ca_name = org["ca"]
        ca_url = config.url(ca_name)
        info = ca_client.cainfo(ca_url)
        say(f"{msp_id} ({org['name']}) -- issued by {ca_name}")
        registrar = ca_client.enroll(ca_url, "admin", "adminpw", config.ca_dir(ca_name) / "registrar" / "msp", msp_id)
        ca_client.write_org_msp(config.org_msp_dir(msp_id), info["caCert"])

        for node, spec in topo["nodes"].items():
            if spec["org"] != msp_id or spec["kind"] not in ("peer", "orderer"):
                continue
            cn = config.fqdn(node)
            ca_client.register(ca_url, registrar, cn, f"{node}-pw", spec["kind"])
            ca_client.enroll(ca_url, cn, f"{node}-pw", config.node_msp_dir(node), msp_id,
                             hosts=[cn, spec["host"], "localhost"])
            say(f"  {spec['kind']:<8} {cn}")

        admin_cn = f"Admin@{config.org_domain(msp_id)}"
        ca_client.register(ca_url, registrar, admin_cn, "admin-pw", "admin")
        ca_client.enroll(ca_url, admin_cn, "admin-pw", config.user_msp_dir(msp_id, "Admin"), msp_id)
        say(f"  admin    {admin_cn}")

        for user, role in (org.get("users") or {}).items():
            attrs = {"role": role, "dept": org.get("dept", "")}
            ca_client.register(ca_url, registrar, user, f"{user}-pw", "client", attrs)
            ca_client.enroll(ca_url, user, f"{user}-pw", config.user_msp_dir(msp_id, user), msp_id)
            say(f"  client   {user:<11} role={role:<10} dept={org.get('dept', '')}")


def build_genesis(say: Say = print) -> dict:
    from nodes.configtx import build_genesis_block

    path = config.genesis_block_path()
    if path.exists():
        say(f"reusing existing genesis block {path.name} (never regenerate it for a live channel)")
        return json.loads(path.read_text())
    block = build_genesis_block()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(block, indent=2))
    cfg = block["data"]["config"]
    say(f"genesis block {blk.block_hash(block)[:16]}...: {len(cfg['orgs'])} orgs, "
        f"{len(cfg['orderer']['consenters'])} Raft consenters, chaincode "
        + ", ".join(f"{n} {c['version']} ({c['endorsementPolicy']})" for n, c in cfg["chaincodes"].items()))
    return block


def _join_request(admin: SigningIdentity, block: dict) -> dict:
    return {"block": block, "signer": admin.wire(),
            "signature": admin.sign(canonical({"action": "join", "blockHash": blk.block_hash(block)}))}


def raft_leader(timeout: float = 0) -> tuple[str | None, dict]:
    """Return (leader, statuses) once a majority of orderers agree on a leader."""
    deadline = time.monotonic() + timeout
    while True:
        statuses, votes = {}, {}
        for o in config.nodes_of_kind("orderer"):
            try:
                st = httpx.get(f"{config.url(o)}/status", timeout=2).json()
                statuses[o] = st
                leader = (st.get("raft") or {}).get("leader")
                if leader:
                    votes[leader] = votes.get(leader, 0) + 1
            except Exception:
                statuses[o] = None
        for leader, n in votes.items():
            if n * 2 > len(config.nodes_of_kind("orderer")):
                return leader, statuses
        if time.monotonic() >= deadline:
            return None, statuses
        time.sleep(0.5)


def join_channel(say: Say = print) -> None:
    block = json.loads(config.genesis_block_path().read_text())
    orderer_admin = SigningIdentity.from_msp_dir(config.user_msp_dir("OrdererMSP", "Admin"), "OrdererMSP")
    for o in config.nodes_of_kind("orderer"):
        resp = httpx.post(f"{config.url(o)}/admin/join", json=_join_request(orderer_admin, block), timeout=10)
        if resp.status_code != 200:
            raise RuntimeError(f"{o} refused to join: {error_message(resp)}")
        say(f"{o} joined {config.channel_name()}" + (" (already)" if resp.json().get("already") else ""))
    leader, statuses = raft_leader(timeout=30)
    if not leader:
        raise RuntimeError("the Raft cluster did not elect a leader within 30 s")
    term = (statuses[leader] or {}).get("raft", {}).get("term")
    say(f"Raft leader elected: {leader} (term {term})")

    for msp_id in config.application_orgs():
        admin = SigningIdentity.from_msp_dir(config.user_msp_dir(msp_id, "Admin"), msp_id)
        for peer in config.topology()["organizations"][msp_id]["peers"]:
            resp = httpx.post(f"{config.url(peer)}/admin/join", json=_join_request(admin, block), timeout=10)
            if resp.status_code != 200:
                raise RuntimeError(f"{peer} refused to join: {error_message(resp)}")
            say(f"{peer} joined (signed by {admin.name})" + (" (already)" if resp.json().get("already") else ""))
