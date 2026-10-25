#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# landchain.sh -- day-to-day control of individual processes.
#
#   ./landchain.sh status              who is running, Raft leader, peer heights
#   ./landchain.sh restart <name>      e.g. restart peer0.org2 after editing nodes/peer/*.py
#   ./landchain.sh kill <name>         SIGKILL one node (try: kill the Raft leader)
#   ./landchain.sh logs <name>         follow logs/<name>.log
#   ./landchain.sh demo                run the demo transactions
#   ./landchain.sh check               one transfer through the dashboards' REST API, checked
#   ./landchain.sh check-agent         the LangGraph validator vs a misbehaving model (redraft, fallback, injection, kill+resume)
#   ./landchain.sh check-web           the web app's pages, metadata, 404s, robots/sitemap/manifest, gateway proxy
#   ./landchain.sh attest-agent        (re)register the validator's PGP key on the ledger
#   ./landchain.sh attest-records      (re)register the Records Office key that signs transfer records
#   ./landchain.sh geo-rebuild         rebuild the geospatial database (GeoPackage) from the ledger
#
# Names: ca-orderer ca-org1 ca-org2 ca-org3 orderer1 orderer2 orderer3
#        peer0.org1 peer0.org2 peer0.org3 digilocker agent gateway web
# ---------------------------------------------------------------------------
set -eu
. "$(dirname "$0")/lib/common.sh"
if [ $# -eq 0 ]; then sed -n '2,18p' "$0"; exit 0; fi
ctl "$@"
