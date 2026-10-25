#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# cc.sh — call the landrec chaincode as a specific certificate holder.
# Runs inside the cli container.
#
#   cc.sh invoke <org> <user> <Function> [args...]   write; waits for commit
#   cc.sh query  <org> <user> <Function> [args...]   read; prints JSON
#
# Examples (from the host):
#   docker exec cli bash /work/network/scripts/cc.sh query 1 clerk1 WhoAmI
#   docker exec cli bash /work/network/scripts/cc.sh query 2 verifier1 ListApplications ALL
#
# Exit code is non-zero if the chaincode rejects the call (e.g. ACCESS DENIED),
# and the chaincode's own error message is printed.
# ---------------------------------------------------------------------------
set -uo pipefail
source /work/network/scripts/env.sh

mode=${1:?usage: cc.sh invoke|query <org> <user> <Function> [args...]}
org=${2:?org}; user=${3:?user}; fn=${4:?Function}; shift 4

json_str() {
  local s=$1
  s=${s//\\/\\\\}; s=${s//\"/\\\"}; s=${s//$'\n'/\\n}; s=${s//$'\r'/\\r}; s=${s//$'\t'/\\t}
  printf '"%s"' "$s"
}

args=""
for a in "$@"; do
  args+="${args:+,}$(json_str "$a")"
done
ctor="{\"function\":\"${fn}\",\"Args\":[${args}]}"

use_identity "$org" "$user" || exit 2

# Pull the chaincode's message out of peer CLI noise.
chaincode_error() {
  local msg
  # message:"..." may itself contain escaped quotes (\")
  msg=$(grep -oE 'message:"([^"\\]|\\.)*"' <<<"$1" | head -n 1 | sed -E 's/^message:"//; s/"$//; s/\\"/"/g')
  if [ -z "$msg" ]; then
    # No chaincode message (e.g. an ordering-service error): first error line only.
    msg=$(grep -m1 '^Error:' <<<"$1" | sed -E 's/ - proposal response:.*//' | cut -c1-300)
  fi
  echo "${msg:-$1}"
}

# Errors that mean the orderer did NOT accept the transaction, so sending it
# again cannot create a duplicate: a Raft election in progress, or the chosen
# orderer being unreachable.
safe_to_retry() {
  grep -qE 'SERVICE_UNAVAILABLE|error sending transaction.*(connection refused|failed to create new connection)' <<<"$1"
}

case "$mode" in
  query)
    out=$(peer chaincode query -C "$CHANNEL" -n "$CC_NAME" -c "$ctor" 2>&1)
    rc=$?
    if [ $rc -ne 0 ]; then echo "$(chaincode_error "$out")" >&2; exit $rc; fi
    echo "$out"
    ;;
  invoke)
    attempt=1
    while :; do
      pick_orderer || exit 1
      out=$(peer chaincode invoke -o "$ORDERER_ADDR" --tls --cafile "$ORDERER_CA" \
            -C "$CHANNEL" -n "$CC_NAME" "${ALL_PEERS[@]}" -c "$ctor" --waitForEvent 2>&1)
      rc=$?
      if [ $rc -eq 0 ] && grep -q "status:200" <<<"$out"; then break; fi
      if safe_to_retry "$out" && [ "$attempt" -lt 15 ]; then
        [ "$attempt" -eq 1 ] && echo "  (ordering service busy -- e.g. Raft electing a new leader; retrying)" >&2
        attempt=$((attempt + 1)); sleep 2; continue
      fi
      echo "$(chaincode_error "$out")" >&2
      exit 1
    done
    txid=$(grep -o 'txid \[[0-9a-f]*\]' <<<"$out" | head -n 1 | tr -d '[]' | awk '{print $2}')
    echo "committed ${txid:+tx ${txid:0:16}...}"
    ;;
  *)
    echo "unknown mode '$mode' (use invoke or query)" >&2; exit 2 ;;
esac
