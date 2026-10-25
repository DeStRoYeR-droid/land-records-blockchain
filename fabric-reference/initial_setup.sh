#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# initial_setup.sh — ONE-TIME creation of the LandChain network.
#
#   1. checks Docker, ports and demo tools
#   2. pulls the Hyperledger Fabric images
#   3. starts one Fabric CA per organisation
#   4. issues X.509 certificates to all three departments + the orderer org:
#        node identities (signing + TLS), org admins, and role-bearing users
#        whose certificates carry role=/dept= attributes
#   5. starts 3 Raft orderers, 3 peers, the CLI and the DigiLocker service
#   6. creates channel `landchannel` and joins every node
#   7. installs, approves and commits the `landrec` chaincode
#   8. smoke-tests it by asking the chaincode who the caller is
#
# Usage:
#   ./initial_setup.sh            first-time setup
#   ./initial_setup.sh --force    wipe everything and set up again
#
# Afterwards use ./startup.sh and ./shutdown.sh.
# ---------------------------------------------------------------------------
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT"

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
    *) fail "unknown option: $arg" ;;
  esac
done

banner "LandChain -- initial setup"
START_TS=$(date +%s)

# ---------------------------------------------------------------------------
step 1/8 "Pre-flight checks"
# ---------------------------------------------------------------------------
require_docker
ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null) and Compose available"

if is_initialised; then
  if [ "$FORCE" -eq 1 ]; then
    warn "--force given: wiping the existing network first"
    "$ROOT/shutdown.sh" --clean --yes
  else
    echo
    warn "The network is already set up."
    info "  ./startup.sh              start it and run the demo transactions"
    info "  ./initial_setup.sh --force  wipe everything and start over"
    exit 0
  fi
fi

if [ -d "$ROOT/organizations" ] || docker ps -a --format '{{.Names}}' | grep -qE '^(ca-org[123]|ca-orderer|cli|digilocker|orderer[123]\.landchain|peer0\.org[123]\.landchain)'; then
  warn "Found leftovers of an earlier, incomplete setup -- cleaning them first"
  "$ROOT/shutdown.sh" --clean --yes >/dev/null
fi

busy=()
for p in 12050 12053 12060 12063 12070 12073 12054 12154 12254 12354 12151 12251 12351 8000; do
  if port_in_use "$p"; then busy+=("$p"); fi
done
[ ${#busy[@]} -eq 0 ] || fail "These ports are already in use: ${busy[*]} -- free them and re-run."
ok "Ports 12050-12351 and 8000 are free"

for t in curl jq; do
  command -v "$t" >/dev/null 2>&1 && ok "$t found" \
    || warn "$t not found -- needed later by demo_transactions.sh (macOS: brew install $t)"
done

# ---------------------------------------------------------------------------
step 2/8 "Pulling Hyperledger Fabric images (Fabric ${FABRIC_VERSION}, CA ${CA_VERSION})"
# ---------------------------------------------------------------------------
# fabric-nodeenv is not referenced in the compose file: the peers use it to
# build and run the Node.js chaincode, so it must be present locally.
for img in \
  "hyperledger/fabric-ca:${CA_VERSION}" \
  "hyperledger/fabric-orderer:${FABRIC_VERSION}" \
  "hyperledger/fabric-peer:${FABRIC_VERSION}" \
  "hyperledger/fabric-tools:${FABRIC_VERSION}" \
  "hyperledger/fabric-nodeenv:2.5"; do
  if docker image inspect "$img" >/dev/null 2>&1; then
    ok "$img (cached)"
  else
    info "pulling $img ..."
    docker pull -q "$img" >/dev/null || fail "could not pull $img"
    ok "$img"
  fi
done

# ---------------------------------------------------------------------------
step 3/8 "Starting the four Certificate Authorities"
# ---------------------------------------------------------------------------
mkdir -p organizations/fabric-ca/{ordererOrg,org1,org2,org3} channel-artifacts
dc up -d ca-orderer ca-org1 ca-org2 ca-org3 2>&1 | sed 's/^/  /' | grep -v "^  $" || true
for ca in ordererOrg org1 org2 org3; do
  wait_until "CA ${ca} root certificate" 60 test -s "organizations/fabric-ca/${ca}/ca-cert.pem"
done
ok "ca-orderer :12054   ca-org1 :12154   ca-org2 :12254   ca-org3 :12354"

# ---------------------------------------------------------------------------
step 4/8 "Issuing X.509 certificates"
# ---------------------------------------------------------------------------
docker run --rm --network landchain_net -v "$ROOT:/work" \
  "hyperledger/fabric-ca:${CA_VERSION}" sh /work/network/scripts/issue_certs.sh

echo
info "${C_BOLD}Certificates issued (read back from disk):${C_RESET}"
if command -v openssl >/dev/null 2>&1; then
  printf "  %-36s %-12s %-8s %-10s %s\n" "IDENTITY" "ISSUED BY" "OU" "role" "dept"
  printf "  %-36s %-12s %-8s %-10s %s\n" "--------" "---------" "--" "----" "----"
  show_cert() {
    local label=$1 cert=$2 text ou issuer role dept
    [ -f "$cert" ] || return 0
    # `|| true`: under `set -eo pipefail`, a grep that finds nothing (e.g. no
    # role attribute on an orderer certificate) would otherwise abort the setup.
    text=$(openssl x509 -in "$cert" -noout -text 2>/dev/null) || true
    issuer=$(openssl x509 -in "$cert" -noout -issuer 2>/dev/null | sed -n 's/.*CN *= *\([^,/]*\).*/\1/p') || true
    ou=$(openssl x509 -in "$cert" -noout -subject 2>/dev/null | grep -oE 'OU *= *(admin|client|peer|orderer)' | head -n1 | sed 's/.*= *//') || true
    role=$(grep -o '"role":"[^"]*"' <<<"$text" | cut -d'"' -f4) || true
    dept=$(grep -o '"dept":"[^"]*"' <<<"$text" | cut -d'"' -f4) || true
    printf "  %-36s %-12s %-8s %-10s %s\n" "$label" "${issuer:-?}" "${ou:-?}" "${role:--}" "${dept:--}"
  }
  O=organizations
  for i in 1 2 3; do
    show_cert "orderer${i}.landchain.example" "$O/ordererOrganizations/landchain.example/orderers/orderer${i}.landchain.example/msp/signcerts/cert.pem"
  done
  for n in 1 2 3; do
    D="$O/peerOrganizations/org${n}.landchain.example"
    show_cert "peer0.org${n}.landchain.example" "$D/peers/peer0.org${n}.landchain.example/msp/signcerts/cert.pem"
    show_cert "Admin@org${n}.landchain.example" "$D/users/Admin@org${n}.landchain.example/msp/signcerts/cert.pem"
    for u in registrar1 clerk1 verifier1 surveyor1 collector1; do
      show_cert "${u}@org${n}.landchain.example" "$D/users/${u}@org${n}.landchain.example/msp/signcerts/cert.pem"
    done
  done
else
  warn "openssl not found; skipping the certificate table"
fi

# ---------------------------------------------------------------------------
step 5/8 "Starting orderers, peers, CLI and DigiLocker"
# ---------------------------------------------------------------------------
info "building the DigiLocker image and starting 11 containers ..."
dc up -d --build 2>&1 | grep -E "Started|Running|Created|Built|Error" | sed 's/^/  /' || true
wait_until "cli container" 60 docker exec cli true
wait_until "DigiLocker /health" 90 curl -sf "${DIGILOCKER_URL}/health"
ok "all containers up; DigiLocker answering on ${DIGILOCKER_URL}"

# ---------------------------------------------------------------------------
step 6/8 "Creating channel 'landchannel'"
# ---------------------------------------------------------------------------
cli_script create_channel.sh

# ---------------------------------------------------------------------------
step 7/8 "Deploying chaincode 'landrec'"
# ---------------------------------------------------------------------------
cli_script deploy_chaincode.sh

# ---------------------------------------------------------------------------
step 8/8 "Smoke test -- the chaincode reads the caller's certificate"
# ---------------------------------------------------------------------------
who=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if who=$(cc query 1 clerk1 WhoAmI 2>/dev/null); then break; fi
  sleep 3
done
[ -n "$who" ] || fail "chaincode did not answer WhoAmI -- check: docker logs peer0.org1.landchain.example"
if command -v jq >/dev/null 2>&1; then
  info "clerk1 -> $(jq -c '{name, mspId, role, dept}' <<<"$who")"
else
  info "clerk1 -> $who"
fi
ok "chaincode is live"

touch "$INIT_MARKER"
ELAPSED=$(( $(date +%s) - START_TS ))

banner "Setup complete in $((ELAPSED / 60))m $((ELAPSED % 60))s"
cat <<EOF
  Organisations   Org1MSP Registration | Org2MSP Revenue | Org3MSP Collectorate
  Orderers        3-node Raft (orderer1..3.landchain.example)
  Channel         landchannel
  Chaincode       landrec 1.0  policy AND(Org1, Org2, Org3)
  DigiLocker      ${DIGILOCKER_URL}/docs

  Next:
    ./startup.sh                run the automated land-transfer transactions
    ./shutdown.sh               stop (keeps ledger and certificates)
    ./shutdown.sh --clean       wipe everything
EOF
