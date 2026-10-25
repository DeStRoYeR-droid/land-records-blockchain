"""
LandChain blockchain nodes, written in Python.

This package re-implements the parts of Hyperledger Fabric that the land
registry uses, so every moving part is readable and editable:

    nodes.common   canonical bytes, keys/signatures, X.509, MSPs, policies
    nodes.ca       one Certificate Authority per organisation (CSR enrollment)
    nodes.orderer  Raft consensus + block cutting + block delivery
    nodes.peer     endorse (simulate) -> validate -> commit, world state, events
    nodes.shim     the API a smart contract sees (ctx.stub, ctx.client_identity)
    nodes.sdk      client side: build a proposal, collect endorsements, submit

Run any node on its own, e.g.

    python -m nodes.orderer --name orderer1
    python -m nodes.peer    --name peer0.org2

Endpoints, ports and organisations come from network/nodes.yaml and
network/configtx.yaml. See docs/ARCHITECTURE.md for the full walkthrough.
"""
