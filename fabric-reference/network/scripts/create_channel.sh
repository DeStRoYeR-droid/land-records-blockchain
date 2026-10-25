#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# create_channel.sh — create `landchannel` and join all nodes to it.
# Runs inside the cli container (initial_setup.sh calls it).
#
#   1. configtxgen builds the channel's genesis block from network/configtx.yaml
#   2. osnadmin joins each of the 3 Raft orderers (channel participation API)
#   3. each department's admin joins its own peer
# ---------------------------------------------------------------------------
set -euo pipefail
source /work/network/scripts/env.sh

mkdir -p /work/channel-artifacts
if [ -s "$CHANNEL_BLOCK" ]; then
  # Never regenerate: a new block has a new hash, and nodes that joined with
  # the old one could no longer agree with nodes that join with the new one.
  echo "  -> reusing existing genesis block for '${CHANNEL}'"
else
  echo "  -> building genesis block for '${CHANNEL}'"
  configtxgen -configPath /work/network -profile LandChannel \
    -outputBlock "$CHANNEL_BLOCK" -channelID "$CHANNEL" >/tmp/configtxgen.out 2>&1 \
    || { cat /tmp/configtxgen.out; exit 1; }
  echo "     genesis block written: 3 orgs, 3 Raft consenters"
fi

join_orderer() {  # <node> <tls-dir>; succeeds if joined now or already
  # osnadmin exits 0 even when the orderer answers with an HTTP error,
  # so the "Status:" line is the only reliable signal.
  local out
  out=$(osnadmin channel join --channelID "$CHANNEL" --config-block "$CHANNEL_BLOCK" \
        -o "$1:7053" --ca-file "$2/ca.crt" --client-cert "$2/server.crt" --client-key "$2/server.key" 2>&1)
  case "$out" in
    *"Status: 201"*)     return 0 ;;
    *"already exists"*)  echo "     (already joined)"; return 0 ;;
  esac
  echo "$out" > /tmp/osnadmin.out
  return 1
}

for i in 1 2 3; do
  node="orderer${i}.${DOMAIN}"
  tls="${ORG_ROOT}/ordererOrganizations/${DOMAIN}/orderers/${node}/tls"
  echo "  -> joining ${node}"
  retry 10 3 join_orderer "$node" "$tls" || { cat /tmp/osnadmin.out; exit 1; }
done

echo "  -> waiting for the Raft cluster to elect a leader"
tls1="${ORG_ROOT}/ordererOrganizations/${DOMAIN}/orderers/orderer1.${DOMAIN}/tls"
raft_active() {
  osnadmin channel list --channelID "$CHANNEL" -o "orderer1.${DOMAIN}:7053" \
    --ca-file "${tls1}/ca.crt" --client-cert "${tls1}/server.crt" --client-key "${tls1}/server.key" 2>/dev/null \
    | grep -q '"status": *"active"'
}
retry 20 2 raft_active || { echo "Raft cluster did not become active"; exit 1; }
echo "     Raft leader elected, channel active on the ordering service"

for org in 1 2 3; do
  use_identity "$org" Admin
  if peer channel list 2>/dev/null | grep -qx "$CHANNEL"; then
    echo "  -> peer0.org${org} already on '${CHANNEL}'"
    continue
  fi
  echo "  -> peer0.org${org} joining '${CHANNEL}' (as Admin@org${org})"
  # Join with block 0 as the ordering service holds it, so the peer's chain
  # is guaranteed to match the orderers' chain.
  fetched="/tmp/${CHANNEL}-org${org}-genesis.block"
  retry 10 3 peer channel fetch 0 "$fetched" -c "$CHANNEL" -o "$ORDERER_ADDR" \
    --tls --cafile "$ORDERER_CA" >/tmp/fetch.out 2>&1 || { cat /tmp/fetch.out; exit 1; }
  retry 10 3 peer channel join -b "$fetched" >/tmp/join.out 2>&1 || { cat /tmp/join.out; exit 1; }
done

use_identity 1 Admin
retry 10 2 bash -c "peer channel getinfo -c '$CHANNEL' >/dev/null 2>&1" || true
echo "  -> channel height as seen by peer0.org1: $(peer channel getinfo -c "$CHANNEL" 2>/dev/null | sed -n 's/.*"height":\([0-9]*\).*/\1/p')"
