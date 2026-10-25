#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# startup.sh — start the LandChain network and run the automated transactions.
#
# Brings every container back up with its ledger, certificates and chaincode
# intact, waits until the peers and DigiLocker answer, then runs
# demo_transactions.sh.
#
# Usage:
#   ./startup.sh             start + run the demo transactions
#   ./startup.sh --no-demo   start only
#
# Run ./initial_setup.sh once before the first startup.
# ---------------------------------------------------------------------------
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT"

RUN_DEMO=1
for arg in "$@"; do
  case "$arg" in
    --no-demo) RUN_DEMO=0 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) fail "unknown option: $arg" ;;
  esac
done

banner "LandChain -- startup"
require_docker
is_initialised || fail "The network has not been set up yet. Run ./initial_setup.sh first."

step 1/3 "Starting containers"
dc up -d 2>&1 | grep -E "Started|Running|Error" | sed 's/^/  /' || true

step 2/3 "Waiting for the network to be ready"
wait_until "cli container" 60 docker exec cli true
wait_until "DigiLocker /health" 90 curl -sf "${DIGILOCKER_URL}/health"
ok "DigiLocker answering on ${DIGILOCKER_URL}"
# The first query after a restart relaunches the chaincode container on the peer.
wait_until "peers and chaincode answering" 120 cc query 1 clerk1 WhoAmI
ok "peers joined to landchannel, chaincode landrec answering"

step 3/3 "Network status"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' \
  | grep -E 'NAMES|landchain|^ca-|^cli|^digilocker|dev-peer' \
  | sed -E 's/0\.0\.0\.0://g; s/\[::\]:[0-9]+->[0-9]+\/tcp,? ?//g' | sed 's/^/  /' || true

if [ "$RUN_DEMO" -eq 1 ]; then
  "$ROOT/demo_transactions.sh"
else
  echo
  info "Network is up. Run ./demo_transactions.sh whenever you're ready."
fi
