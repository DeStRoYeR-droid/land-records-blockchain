"""
Where things are: repo paths, node addresses, MSP directories.

Everything is derived from network/nodes.yaml so there is exactly one place to
edit when a node moves.

Directory layout (same shape as Fabric's, so Fabric tutorials still apply):

    organizations/
      fabric-ca/<ca-name>/               CA root key + cert, CA's registry
      ordererOrganizations/landchain.example/
        msp/                             the org MSP (root cert, config.yaml)
        orderers/orderer1.landchain.example/msp/{signcerts,keystore,cacerts}
        users/Admin@landchain.example/msp/
      peerOrganizations/org1.landchain.example/
        msp/
        peers/peer0.org1.landchain.example/msp/
        users/Admin@org1.landchain.example/msp/
        users/clerk1@org1.landchain.example/msp/
    channel-artifacts/landchannel.block  the genesis block
    data/<node-name>/                    each node's ledger (SQLite)
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("LANDCHAIN_ROOT", Path(__file__).resolve().parents[2]))
NETWORK_DIR = ROOT / "network"
ORGS_DIR = ROOT / "organizations"
ARTIFACTS_DIR = ROOT / "channel-artifacts"
DATA_DIR = ROOT / "data"


@lru_cache(maxsize=1)
def topology() -> dict[str, Any]:
    with open(NETWORK_DIR / "nodes.yaml") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def configtx() -> dict[str, Any]:
    with open(NETWORK_DIR / "configtx.yaml") as fh:
        return yaml.safe_load(fh)


def domain() -> str:
    return topology()["domain"]


def channel_name() -> str:
    return topology()["channel"]


def node(name: str) -> dict[str, Any]:
    topo = topology()
    if name in topo["nodes"]:
        return {"name": name, **topo["nodes"][name]}
    if name in topo["services"]:
        return {"name": name, "kind": "service", **topo["services"][name]}
    raise KeyError(f"unknown node or service '{name}' (see network/nodes.yaml)")


def url(name: str) -> str:
    n = node(name)
    env = os.environ.get(f"LANDCHAIN_URL_{name.upper().replace('.', '_').replace('-', '_')}")
    return env or f"http://{n['host']}:{n['port']}"


def nodes_of_kind(kind: str) -> list[str]:
    return [name for name, spec in topology()["nodes"].items() if spec["kind"] == kind]


def org_of(name: str) -> str:
    return node(name)["org"]


def fqdn(name: str) -> str:
    """peer0.org1 -> peer0.org1.landchain.example (the certificate's CN)."""
    return f"{name}.{domain()}"


def org_domain(msp_id: str) -> str:
    return topology()["organizations"][msp_id]["domain"]


def org_root_dir(msp_id: str) -> Path:
    kind = "ordererOrganizations" if msp_id == "OrdererMSP" else "peerOrganizations"
    return ORGS_DIR / kind / org_domain(msp_id)


def node_msp_dir(name: str) -> Path:
    msp_id = org_of(name)
    group = "orderers" if node(name)["kind"] == "orderer" else "peers"
    return org_root_dir(msp_id) / group / fqdn(name) / "msp"


def user_msp_dir(msp_id: str, user: str) -> Path:
    """user='Admin' gives the org admin; anything else is a named user."""
    return org_root_dir(msp_id) / "users" / f"{user}@{org_domain(msp_id)}" / "msp"


def org_msp_dir(msp_id: str) -> Path:
    return org_root_dir(msp_id) / "msp"


def ca_dir(ca_name: str) -> Path:
    return ORGS_DIR / "fabric-ca" / ca_name


def data_dir(name: str) -> Path:
    path = DATA_DIR / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def genesis_block_path() -> Path:
    return ARTIFACTS_DIR / f"{channel_name()}.block"


def application_orgs() -> list[str]:
    return list(configtx()["application"]["orgs"])


def org_peer(msp_id: str) -> str:
    return topology()["organizations"][msp_id]["peers"][0]


def bind_host(name: str) -> str:
    return os.environ.get("LANDCHAIN_BIND", node(name).get("bind", "127.0.0.1"))
