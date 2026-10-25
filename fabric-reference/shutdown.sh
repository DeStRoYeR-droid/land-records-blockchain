#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# shutdown.sh — stop the LandChain network.
#
#   ./shutdown.sh            stop every container; the ledger, certificates and
#                            chaincode are kept, so ./startup.sh resumes exactly
#                            where you left off
#   ./shutdown.sh --clean    delete EVERYTHING: containers, ledger volumes,
#                            chaincode images, certificates, channel artifacts.
#                            You will need ./initial_setup.sh again.
#   --yes                    skip the confirmation prompt for --clean
# ---------------------------------------------------------------------------
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT"

CLEAN=0; YES=0
for arg in "$@"; do
  case "$arg" in
    --clean) CLEAN=1 ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) fail "unknown option: $arg" ;;
  esac
done

require_docker

# Chaincode containers are started by the peers, not by Compose.
cc_containers() { docker ps -aq --filter "name=dev-peer0.org" 2>/dev/null || true; }

if [ "$CLEAN" -eq 0 ]; then
  banner "LandChain -- shutdown (data preserved)"
  step 1/2 "Stopping chaincode containers"
  ids=$(cc_containers)
  if [ -n "$ids" ]; then docker stop $ids >/dev/null; ok "stopped $(echo "$ids" | wc -w | tr -d ' ')"; else info "none running"; fi

  step 2/2 "Stopping network containers"
  dc stop 2>&1 | grep -E "Stopped|Error" | sed 's/^/  /' || true
  ok "stopped. Ledger, certificates and chaincode are kept."
  info "Resume with: ./startup.sh"
  exit 0
fi

banner "LandChain -- FULL RESET"
if [ "$YES" -eq 0 ]; then
  echo "  This deletes the ledger, every certificate, and the chaincode images."
  read -r -p "  Type 'yes' to continue: " answer
  [ "$answer" = "yes" ] || { info "aborted"; exit 0; }
fi

step 1/4 "Removing network containers and ledger volumes"
dc down -v --remove-orphans 2>&1 | grep -E "Removed|Error" | sed 's/^/  /' || true
ok "containers and volumes removed"

step 2/4 "Removing chaincode containers and images"
ids=$(cc_containers)
[ -z "$ids" ] || docker rm -f $ids >/dev/null
imgs=$(docker images -q --filter "reference=dev-peer0.org*" 2>/dev/null || true)
[ -z "$imgs" ] || docker rmi -f $imgs >/dev/null 2>&1 || true
ok "chaincode containers and images removed"

step 3/4 "Deleting certificates and channel artifacts"
# The CA and CLI containers wrote these files as root. Deleting them from a
# container avoids needing sudo on Linux.
if [ -d organizations ] || [ -d channel-artifacts ]; then
  docker run --rm -v "$ROOT:/work" "hyperledger/fabric-tools:${FABRIC_VERSION}" \
    rm -rf /work/organizations /work/channel-artifacts 2>/dev/null \
    || rm -rf organizations channel-artifacts
fi
ok "organizations/ and channel-artifacts/ deleted"

step 4/4 "Removing the DigiLocker image"
docker rmi -f landchain/digilocker:1.0 >/dev/null 2>&1 || true
ok "done"
info "Set up again with: ./initial_setup.sh"
