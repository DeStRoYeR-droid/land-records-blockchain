#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# deploy_chaincode.sh — Fabric chaincode lifecycle for `landrec`.
# Runs inside the cli container (initial_setup.sh calls it).
#
#   package -> install on all 3 peers -> each org approves -> commit
#
# Endorsement policy: AND('Org1MSP.peer','Org2MSP.peer','Org3MSP.peer')
# -> no single department can write to the ledger on its own.
#
# CC_PACKAGE_FILE=<path>  use a pre-built package instead of packaging
#                         /work/chaincode/landrec (used by the test harness).
# ---------------------------------------------------------------------------
set -euo pipefail
source /work/network/scripts/env.sh

use_identity 1 Admin
if peer lifecycle chaincode querycommitted -C "$CHANNEL" -n "$CC_NAME" >/dev/null 2>&1; then
  echo "  -> '${CC_NAME}' is already committed on '${CHANNEL}'; nothing to do"
  peer lifecycle chaincode querycommitted -C "$CHANNEL" -n "$CC_NAME" | sed 's/^/     /'
  exit 0
fi

PKG="${CC_PACKAGE_FILE:-/work/channel-artifacts/${CC_NAME}.tar.gz}"
if [ -z "${CC_PACKAGE_FILE:-}" ]; then
  echo "  -> packaging /work/chaincode/${CC_NAME} (Node.js)"
  peer lifecycle chaincode package "$PKG" --path "/work/chaincode/${CC_NAME}" \
    --lang node --label "${CC_NAME}_${CC_VERSION}"
fi
PKG_ID=$(peer lifecycle chaincode calculatepackageid "$PKG")
echo "     package id: ${PKG_ID}"

for org in 1 2 3; do
  use_identity "$org" Admin
  if peer lifecycle chaincode queryinstalled 2>/dev/null | grep -q "$PKG_ID"; then
    echo "  -> already installed on peer0.org${org}"
    continue
  fi
  echo "  -> installing on peer0.org${org} (the peer builds the Node image; first time takes 1-3 min)"
  peer lifecycle chaincode install "$PKG" >/tmp/install.out 2>&1 || { cat /tmp/install.out; exit 1; }
done

for org in 1 2 3; do
  use_identity "$org" Admin
  echo "  -> Org${org}MSP approving the definition"
  peer lifecycle chaincode approveformyorg -o "$ORDERER_ADDR" --tls --cafile "$ORDERER_CA" \
    --channelID "$CHANNEL" --name "$CC_NAME" --version "$CC_VERSION" --sequence "$CC_SEQUENCE" \
    --package-id "$PKG_ID" --signature-policy "$CC_POLICY" --waitForEvent >/tmp/approve.out 2>&1 \
    || { cat /tmp/approve.out; exit 1; }
done

use_identity 1 Admin
echo "  -> commit readiness:"
peer lifecycle chaincode checkcommitreadiness --channelID "$CHANNEL" --name "$CC_NAME" \
  --version "$CC_VERSION" --sequence "$CC_SEQUENCE" --signature-policy "$CC_POLICY" \
  | sed 's/^/     /'

echo "  -> committing the definition (endorsed by all three departments)"
peer lifecycle chaincode commit -o "$ORDERER_ADDR" --tls --cafile "$ORDERER_CA" \
  --channelID "$CHANNEL" --name "$CC_NAME" --version "$CC_VERSION" --sequence "$CC_SEQUENCE" \
  --signature-policy "$CC_POLICY" "${ALL_PEERS[@]}" --waitForEvent >/tmp/commit.out 2>&1 \
  || { cat /tmp/commit.out; exit 1; }

peer lifecycle chaincode querycommitted -C "$CHANNEL" -n "$CC_NAME" | sed 's/^/     /'
