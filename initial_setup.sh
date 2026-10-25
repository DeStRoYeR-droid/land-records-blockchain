#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# initial_setup.sh -- ONE-TIME creation of the LandChain network.
#
#   1. pre-flight checks (Python 3.10+, ports 13050-13950 free)
#   2. starts the four Certificate Authorities (ca-orderer, ca-org1..3)
#   3. issues every X.509 certificate by CSR enrollment: orderers, peers, org
#      admins and role-bearing users (role=/dept= embedded in the certificate)
#   4. builds the genesis block (channel config: orgs, Raft consenters,
#      chaincode definition + endorsement policy AND(Org1, Org2, Org3))
#   5. starts 3 Raft orderers and 3 peers (plain Python processes)
#   6. joins the orderers (a leader gets elected) and the peers to `landchannel`
#   7. smoke-tests the chaincode
#   8. starts DigiLocker, the Agentic AI Validator and the gateway; a Revenue
#      officer attests the validator's PGP key on the ledger
#   9. installs the web app's dependencies (npm)
#
# Usage:
#   ./initial_setup.sh             first-time setup (leaves everything running)
#   ./initial_setup.sh --force     wipe everything and set up again
#   ./initial_setup.sh --no-web    skip the npm install
# ---------------------------------------------------------------------------
set -eu
. "$(dirname "$0")/lib/common.sh"
ctl setup "$@"
