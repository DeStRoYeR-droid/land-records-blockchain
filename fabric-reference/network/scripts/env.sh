#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# env.sh — shared settings for every script that runs inside the `cli`
# container. Source it; don't execute it.
#
#   use_identity <org> [user]   switch the peer CLI to that org and identity
#                               (default user: Admin; others: clerk1, registrar1,
#                               verifier1, surveyor1, collector1)
# ---------------------------------------------------------------------------

export ORG_ROOT=/work/organizations
export DOMAIN=landchain.example
export CHANNEL=landchannel
export CC_NAME=landrec
export CC_VERSION=1.0
export CC_SEQUENCE=1
export CC_POLICY="AND('Org1MSP.peer','Org2MSP.peer','Org3MSP.peer')"

export ORDERER_ADDR="orderer1.${DOMAIN}:7050"
export ORDERER_CA="${ORG_ROOT}/ordererOrganizations/${DOMAIN}/tlsca/tlsca.${DOMAIN}-cert.pem"
export CHANNEL_BLOCK=/work/channel-artifacts/${CHANNEL}.block
export CORE_PEER_TLS_ENABLED=true

peer_addr() { echo "peer0.org$1.${DOMAIN}:7051"; }
peer_tls()  { echo "${ORG_ROOT}/peerOrganizations/org$1.${DOMAIN}/tlsca/tlsca.org$1.${DOMAIN}-cert.pem"; }

use_identity() {
  local org=$1 user=${2:-Admin}
  local msp="${ORG_ROOT}/peerOrganizations/org${org}.${DOMAIN}/users/${user}@org${org}.${DOMAIN}/msp"
  if [ ! -d "$msp" ]; then
    echo "No identity '${user}' in Org${org} (looked for ${msp})" >&2
    return 1
  fi
  export CORE_PEER_LOCALMSPID="Org${org}MSP"
  export CORE_PEER_ADDRESS="$(peer_addr "$org")"
  export CORE_PEER_TLS_ROOTCERT_FILE="$(peer_tls "$org")"
  export CORE_PEER_MSPCONFIGPATH="$msp"
}

# The endorsement policy needs all three departments' peers on every write.
ALL_PEERS=(
  --peerAddresses "$(peer_addr 1)" --tlsRootCertFiles "$(peer_tls 1)"
  --peerAddresses "$(peer_addr 2)" --tlsRootCertFiles "$(peer_tls 2)"
  --peerAddresses "$(peer_addr 3)" --tlsRootCertFiles "$(peer_tls 3)"
)

# Raft keeps working with one orderer down; point clients at one that answers.
pick_orderer() {
  local i
  for i in 1 2 3; do
    if timeout 2 bash -c "</dev/tcp/orderer${i}.${DOMAIN}/7050" 2>/dev/null; then
      export ORDERER_ADDR="orderer${i}.${DOMAIN}:7050"
      return 0
    fi
  done
  echo "No orderer is reachable" >&2
  return 1
}

# retry <attempts> <delay-seconds> <cmd...>
retry() {
  local n=$1 d=$2 i=1; shift 2
  until "$@"; do
    if [ "$i" -ge "$n" ]; then return 1; fi
    i=$((i + 1)); sleep "$d"
  done
}
