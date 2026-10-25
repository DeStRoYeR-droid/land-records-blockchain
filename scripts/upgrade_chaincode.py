#!/usr/bin/env python3
"""
upgrade_chaincode.py -- commit a new chaincode definition to the channel.

Fabric's lifecycle in one step: each organisation's admin "approves" the new
definition (here: signs it), and once a MAJORITY have, it is "committed" as a
config transaction. Every peer then reloads the code and endorses again.

    ./landchain.sh status                          # peers say MISMATCH after you edit chaincode/
    .venv/bin/python scripts/upgrade_chaincode.py  # sequence +1, packageHash of the code on disk
    .venv/bin/python scripts/upgrade_chaincode.py --signers Org1MSP          # 1 of 3: refused
    .venv/bin/python scripts/upgrade_chaincode.py --policy "OutOf(2,'Org1MSP.peer','Org2MSP.peer','Org3MSP.peer')"

All three admins' keys live on this machine for convenience. In a real
deployment each department's admin signs on their own machine; only the
MAJORITY rule matters.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from nodes.common import config, policy  # noqa: E402
from nodes.common.msp import SigningIdentity  # noqa: E402
from nodes.peer.chaincode import package_hash  # noqa: E402
from nodes.sdk import CommitError, Gateway  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="landrec")
    parser.add_argument("--version", help="new version label (default: bump the minor version)")
    parser.add_argument("--policy", help="new endorsement policy (default: keep the current one)")
    parser.add_argument("--signers", default=",".join(config.application_orgs()),
                        help="which org admins sign (comma-separated MSP IDs)")
    args = parser.parse_args()

    home = config.org_peer("Org1MSP")
    current = httpx.get(f"{config.url(home)}/channel/config", timeout=5).json()
    old = current["chaincodes"][args.name]
    new_hash = package_hash(old["module"])
    if new_hash == old["packageHash"] and not args.policy and not args.version:
        print(f"chaincode {args.name} on disk already matches the committed definition (sequence {old['sequence']})")
        return
    if args.policy:
        policy.parse(args.policy)
    major, _, minor = old["version"].partition(".")
    version = args.version or f"{major}.{int(minor or 0) + 1}"
    definition = {**old, "version": version, "sequence": old["sequence"] + 1, "packageHash": new_hash,
                  "endorsementPolicy": args.policy or old["endorsementPolicy"]}
    print(f"{args.name}: sequence {old['sequence']} -> {definition['sequence']}, version {old['version']} -> {version}")
    print(f"  package {old['packageHash'][:16]}... -> {new_hash[:16]}...")
    print(f"  policy  {definition['endorsementPolicy']}")

    signers = [SigningIdentity.from_msp_dir(config.user_msp_dir(m, "Admin"), m) for m in args.signers.split(",") if m]
    print(f"  approved (signed) by: {', '.join(s.name for s in signers)}")
    gw = Gateway.for_identity(signers[0])
    try:
        status = await gw.submit_config_update(
            {"kind": "chaincode_definition", "chaincode": args.name, "definition": definition,
             "sequence": current["sequence"] + 1}, signers)
    except (CommitError, Exception) as exc:
        print(f"  REFUSED: {exc}")
        raise SystemExit(1)
    finally:
        await gw.close()
    print(f"  committed in config block {status['block']}")
    deadline = time.monotonic() + 20
    for peer in config.nodes_of_kind("peer"):
        while time.monotonic() < deadline:
            st = httpx.get(f"{config.url(peer)}/status", timeout=5).json()
            cc = st["chaincodes"].get(args.name, {})
            if cc.get("sequence") == definition["sequence"]:
                print(f"  {peer}: sequence {cc['sequence']}, installed package {'matches' if cc['matches'] else 'DIFFERS'}")
                break
            time.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
