"""
configtxgen: build the channel's genesis block.

    python -m nodes.configtx            writes channel-artifacts/landchannel.block

Block 0 holds the channel configuration, which every node treats as law:

  orgs        each application org's MSP (root CA certificate), policies,
              anchor peers
  orderer     the orderer org's MSP, the Raft consenters (address + signing
              certificate), batch size/timeout, Raft timings
  chaincodes  each chaincode's definition: version, sequence, endorsement
              policy and packageHash (SHA-256 of the approved source)

Because block 0 is identical everywhere, every node can verify every other
node's certificates and every block's signatures without trusting anyone.
"""

from __future__ import annotations

import json
import sys

from .common import block as blk
from .common import config, crypto, policy
from .peer.chaincode import package_hash


def build_genesis_config() -> dict:
    tx = config.configtx()
    topo = config.topology()
    orgs = {}
    for msp_id in tx["application"]["orgs"]:
        org = topo["organizations"][msp_id]
        root = (config.org_msp_dir(msp_id) / "cacerts" / "ca.pem").read_text()
        orgs[msp_id] = {
            "name": org["name"], "dept": org.get("dept"), "rootCerts": [root],
            "anchorPeers": [{"name": p, "host": config.node(p)["host"], "port": config.node(p)["port"]}
                            for p in org.get("peers", [])],
            "policies": {
                "Readers": f"OR('{msp_id}.admin','{msp_id}.peer','{msp_id}.client')",
                "Writers": f"OR('{msp_id}.admin','{msp_id}.client')",
                "Admins": f"OR('{msp_id}.admin')",
                "Endorsement": f"OR('{msp_id}.peer')",
            },
        }
    consenters = []
    for name in tx["orderer"]["consenters"]:
        spec = config.node(name)
        cert = (config.node_msp_dir(name) / "signcerts" / "cert.pem").read_text()
        consenters.append({"id": name, "host": spec["host"], "port": spec["port"], "cert": cert})
    chaincodes = {}
    for name, cc in tx.get("chaincodes", {}).items():
        policy.parse(cc["endorsementPolicy"])  # fail early on a typo
        chaincodes[name] = {"version": str(cc["version"]), "sequence": int(cc["sequence"]),
                            "module": cc["module"], "endorsementPolicy": cc["endorsementPolicy"],
                            "packageHash": package_hash(cc["module"])}
    orderer_root = (config.org_msp_dir("OrdererMSP") / "cacerts" / "ca.pem").read_text()
    return {
        "channel": tx["channel"],
        "sequence": 0,
        "createdAt": crypto.iso(crypto.utcnow()),
        "orgs": orgs,
        "orderer": {
            "type": tx["orderer"]["type"],
            "org": {"mspId": "OrdererMSP", "name": topo["organizations"]["OrdererMSP"]["name"],
                    "rootCerts": [orderer_root]},
            "consenters": consenters,
            "batch": tx["orderer"]["batch"],
            "raft": tx["orderer"]["raft"],
        },
        "policies": tx["application"]["policies"],
        "chaincodes": chaincodes,
    }


def build_genesis_block() -> dict:
    return blk.make_block(0, blk.GENESIS_PREVIOUS_HASH, {"kind": "config", "config": build_genesis_config()})


def main() -> None:
    out = config.genesis_block_path()
    if out.exists() and "--force" not in sys.argv:
        print(f"{out} already exists; never regenerate it for a running channel (use --force to overwrite)")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    block = build_genesis_block()
    out.write_text(json.dumps(block, indent=2))
    cfg = block["data"]["config"]
    print(f"genesis block written: {out}")
    print(f"  hash        {blk.block_hash(block)}")
    print(f"  orgs        {', '.join(cfg['orgs'])}")
    print(f"  consenters  {', '.join(c['id'] for c in cfg['orderer']['consenters'])}")
    for name, cc in cfg["chaincodes"].items():
        print(f"  chaincode   {name} {cc['version']} seq {cc['sequence']}  {cc['endorsementPolicy']}")


if __name__ == "__main__":
    main()
