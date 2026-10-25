"""
The Agentic AI Validator -- a virtual Revenue officer at the Org1 -> Org2 handoff.

It gathers evidence with tools (ledger queries, DigiLocker pulls), runs fixed
checks with follow-up investigation, asks a local oMLX model to EXPLAIN the
results, signs the report with OpenPGP, stores it under an IPFS-compatible
CID and anchors its hash on the ledger. It never decides: its certificate
(Org2MSP, role=agent) is refused by every decision function in the chaincode.

    python -m agent            run the service (port 13950)
    agent/knowledge/           the architecture notes the explanations draw on
    agent/checks.py            the rules -- start here to add a check
"""
