#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# demo_transactions.sh — automated end-to-end land transfer on LandChain.
#
# Every write below is signed by a real X.509 identity issued by that
# department's own CA, endorsed by all three departments' peers, ordered by
# the Raft cluster, and committed to the ledger.
#
#   Act 1  Identities      who each certificate says it is
#   Act 2  DigiLocker      citizens, issued documents, consent + OTP
#   Act 3  Registration    parcel on record, transfer filed, documents anchored
#   Act 4  Revenue         independent re-verification, field survey
#   Act 5  Collectorate    human approval; ownership moves on the ledger
#   Act 6  Attacks         what the system refuses, and why
#   Act 7  Audit           public document check, ownership history
#
# LATER: each `tx`/`q` call below becomes an API call triggered by a user in
# the web app. The chaincode and the network do not change.
#
# Usage: ./demo_transactions.sh      (network must be up: ./startup.sh --no-demo)
# ---------------------------------------------------------------------------
set -euo pipefail
source "$(dirname "$0")/lib/common.sh"
cd "$ROOT"

command -v jq   >/dev/null 2>&1 || fail "jq is required (macOS: brew install jq)"
command -v curl >/dev/null 2>&1 || fail "curl is required"
curl -sf "${DIGILOCKER_URL}/health" >/dev/null || fail "DigiLocker is not reachable at ${DIGILOCKER_URL}. Run ./startup.sh --no-demo first."

RUN=$(date +%H%M%S)
PAUSE="${DEMO_PAUSE:-0}"   # e.g. DEMO_PAUSE=2 ./demo_transactions.sh for a slower live demo

ORG_NAME=( "" "Org1 Registration" "Org2 Revenue" "Org3 Collectorate" )

# --- helpers ---------------------------------------------------------------
act()   { echo; echo "${C_BOLD}${C_BLUE}---- $* ----${C_RESET}"; sleep "$PAUSE"; }
who()   { printf "  ${C_DIM}[%-17s %-10s]${C_RESET} " "${ORG_NAME[$1]}" "$2"; }
note()  { echo "  ${C_DIM}$*${C_RESET}"; }

# tx <org> <user> <Function> [args...]  -- must succeed
tx() {
  local org=$1 user=$2 fn=$3; shift 3
  who "$org" "$user"; printf "%s " "$fn"
  local out
  if out=$(cc invoke "$org" "$user" "$fn" "$@" 2>&1); then
    echo "${C_GREEN}-> ${out}${C_RESET}"
  else
    echo "${C_RED}-> FAILED${C_RESET}"; echo "     $out"; exit 1
  fi
}

# denied <org> <user> <Function> [args...]  -- must be REJECTED by the chaincode
denied() {
  local org=$1 user=$2 fn=$3; shift 3
  who "$org" "$user"; printf "%s " "$fn"
  local out
  if out=$(cc invoke "$org" "$user" "$fn" "$@" 2>&1); then
    echo "${C_RED}-> ACCEPTED -- this should have been refused!${C_RESET}"; exit 1
  fi
  echo "${C_YELLOW}-> REFUSED${C_RESET}"
  echo "     ${C_YELLOW}${out}${C_RESET}"
}

# q <org> <user> <Function> [args...]  -- read-only query, prints JSON
q() { cc query "$@"; }

# DigiLocker consent flow: request -> citizen approves with OTP -> fetch.
# dl_fetch <requester> <digilocker-id> <doc_type> <org_name>   -> document JSON
dl_fetch() {
  local req_id
  req_id=$(curl -sf -X POST "${DIGILOCKER_URL}/consent/request" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg r "$1" '{requester_name:$r, redirect_url:"https://landchain.example/callback"}')" | jq -r .id)
  curl -sf -X POST "${DIGILOCKER_URL}/consent/${req_id}/verify-otp" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg d "$2" '{digilocker_id:$d, otp:"123456"}')" >/dev/null
  curl -sf -X POST "${DIGILOCKER_URL}/consent/${req_id}/document" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg t "$3" --arg o "$4" '{doc_type:$t, org_name:$o, consent:"Y"}')"
}

owner_ref() { printf 'landchain-owner:%s' "$1" | sha256_stdin; }

# ===========================================================================
banner "LandChain -- automated land transfer  (run ${RUN})"

act "Act 1 · Identities -- what each X.509 certificate tells the chaincode"
for pair in "1 registrar1" "1 clerk1" "2 verifier1" "2 surveyor1" "3 collector1"; do
  set -- $pair
  W=$(q "$1" "$2" WhoAmI)
  printf "    %-12s %-9s role=%-10s dept=%s\n" \
    "$(jq -r .name <<<"$W")" "$(jq -r .mspId <<<"$W")" "$(jq -r .role <<<"$W")" "$(jq -r .dept <<<"$W")"
done
note "role and dept are X.509 attributes stamped by each department's own CA at enrollment."

# ===========================================================================
act "Act 2 · DigiLocker -- citizens and their government-issued documents"
SELLER=$(curl -sf -X POST "${DIGILOCKER_URL}/users/signup" -H 'Content-Type: application/json' \
  -d '{"name":"Ramesh Kumar","mobile":"9876500001","aadhaar_last4":"4321"}')
BUYER=$(curl -sf -X POST "${DIGILOCKER_URL}/users/signup" -H 'Content-Type: application/json' \
  -d '{"name":"Lakshmi Devi","mobile":"9876500002","aadhaar_last4":"8765"}')
SELLER_DL=$(jq -r .digilockerId <<<"$SELLER"); BUYER_DL=$(jq -r .digilockerId <<<"$BUYER")
SELLER_REF=$(owner_ref "$SELLER_DL");        BUYER_REF=$(owner_ref "$BUYER_DL")
info "Seller  Ramesh Kumar   ${SELLER_DL}   ownerRef ${SELLER_REF:0:16}..."
info "Buyer   Lakshmi Devi   ${BUYER_DL}   ownerRef ${BUYER_REF:0:16}..."

SURVEY="SY-${RUN}-42/1A"
SRO="Sub-Registrar Office, Hanamkonda"
DEED_TEXT="SALE DEED
Survey No: ${SURVEY}, Kazipet, Warangal, Telangana
Extent: 2400 sq.m
Vendor: Ramesh Kumar
Purchaser: Lakshmi Devi
Consideration: Rs. 45,00,000
Registered at ${SRO}"
EC_TEXT="ENCUMBRANCE CERTIFICATE
Survey No: ${SURVEY}
Period searched: 2011-2026
Result: NIL encumbrance"

issue() {
  curl -sf -X POST "${DIGILOCKER_URL}/issuer/documents" -H 'Content-Type: application/json' \
    -d "$(jq -n --arg id "$1" --arg t "$2" --arg o "$3" --arg d "$4" --arg c "$5" \
          '{digilocker_id:$id, doc_type:$t, org_name:$o, description:$d, content:$c}')"
}
DEED=$(issue "$SELLER_DL" SALEDEED "$SRO" "Sale deed for ${SURVEY}" "$DEED_TEXT")
EC=$(issue   "$SELLER_DL" ENCUMBRANCE "$SRO" "Encumbrance certificate for ${SURVEY}" "$EC_TEXT")
info "Issued to seller's locker: SALEDEED $(jq -r .docId <<<"$DEED")  sha256 $(jq -r .contentSha256 <<<"$DEED" | cut -c1-16)..."
info "                           ENCUMBRANCE $(jq -r .docId <<<"$EC")  sha256 $(jq -r .contentSha256 <<<"$EC" | cut -c1-16)..."
note "The documents stay in DigiLocker. Only their SHA-256 fingerprints will touch the ledger."

# ===========================================================================
act "Act 3 · Org1 Registration -- parcel on record, transfer filed, documents anchored"
APP="APP-${RUN}-001"
tx 1 registrar1 RegisterParcel "$SURVEY" "Kazipet" "Warangal" "2400" "Ramesh Kumar" "$SELLER_REF"
tx 1 clerk1 SubmitTransferApplication "$APP" "$SURVEY" "$SELLER_REF" "Lakshmi Devi" "$BUYER_REF" "4500000"

note "Clerk requests the seller's documents through DigiLocker (seller approves with OTP):"
for dt in SALEDEED ENCUMBRANCE; do
  DOC=$(dl_fetch "Registration Dept (Org1)" "$SELLER_DL" "$dt" "$SRO")
  tx 1 clerk1 AnchorDocument "$APP" "$(jq -r .contentSha256 <<<"$DOC")" "$(jq -r .docId <<<"$DOC")" "$dt" "$SRO"
done
tx 1 registrar1 ForwardToRevenue "$APP"
note "Org1 -> Org2 handoff. (This is where the Agentic AI validator will attach.)"

# ===========================================================================
act "Act 4 · Org2 Revenue -- independent verification and field survey"
note "Verifier fetches the documents again through its OWN consent request and hashes them locally:"
HASHES=()
for dt in SALEDEED ENCUMBRANCE; do
  H=$(dl_fetch "Revenue Dept (Org2)" "$SELLER_DL" "$dt" "$SRO" | jq -j .content | sha256_stdin)
  info "  ${dt}  recomputed sha256 ${H:0:16}..."
  HASHES+=("$H")
done
tx 2 verifier1 VerifyDocuments "$APP" "$(printf '%s\n' "${HASHES[@]}" | jq -R . | jq -sc .)"
tx 2 surveyor1 RecordSurvey "$APP" "2412" "N: 30ft road | E: SY-43 | S: canal | W: SY-41"
tx 2 verifier1 ForwardToCollectorate "$APP"
q 2 verifier1 ReadApplication "$APP" | jq -r '"  verification: \(.verification.result)  (\(.verification.documentsChecked) documents)\n  survey:       measured \(.survey.measuredAreaSqm) sq.m vs recorded \(.survey.recordedAreaSqm) sq.m  -> deviation \(.survey.deviationPct)% (tolerance \(.survey.tolerancePct)%)"'

# ===========================================================================
act "Act 5 · Org3 Collectorate -- the human decision"
tx 3 collector1 ApproveTransfer "$APP" "Documents verified, survey within tolerance. Approved."
q 3 collector1 ReadParcel "$SURVEY" | jq -r '"  Parcel \(.surveyNo): owner is now \(.ownerName)  (was \(.lastTransfer.fromName); via \(.lastTransfer.appId))"'

# ===========================================================================
act "Act 6 · Attacks -- what the network refuses"

note "1. Wrong department: a Registration clerk tries to approve a transfer."
denied 1 clerk1 ApproveTransfer "$APP" "self-approval"

note "2. Double sale: the previous owner tries to sell the same parcel again."
denied 1 clerk1 SubmitTransferApplication "APP-${RUN}-002" "$SURVEY" "$SELLER_REF" "Someone Else" "$(owner_ref other)" "100000"

note "3. Tampered document: a second parcel's deed is altered after it was anchored."
SURVEY2="SY-${RUN}-77/2B"
tx 1 registrar1 RegisterParcel "$SURVEY2" "Hanamkonda" "Warangal" "1800" "Lakshmi Devi" "$BUYER_REF"
issue "$BUYER_DL" SALEDEED "$SRO" "Sale deed for ${SURVEY2}" "SALE DEED
Survey No: ${SURVEY2}, Hanamkonda, Warangal
Extent: 1800 sq.m
Vendor: Lakshmi Devi
Purchaser: Ramesh Kumar
Consideration: Rs. 30,00,000" >/dev/null
APP2="APP-${RUN}-003"
tx 1 clerk1 SubmitTransferApplication "$APP2" "$SURVEY2" "$BUYER_REF" "Ramesh Kumar" "$SELLER_REF" "3000000"
DOC2=$(dl_fetch "Registration Dept (Org1)" "$BUYER_DL" SALEDEED "$SRO")
tx 1 clerk1 AnchorDocument "$APP2" "$(jq -r .contentSha256 <<<"$DOC2")" "$(jq -r .docId <<<"$DOC2")" SALEDEED "$SRO"
tx 1 registrar1 ForwardToRevenue "$APP2"
TAMPERED_HASH=$(dl_fetch "Revenue Dept (Org2)" "$BUYER_DL" SALEDEED "$SRO" | jq -j .content \
  | sed 's/1800 sq.m/2800 sq.m/' | sha256_stdin)
note "   the copy Revenue receives says 2800 sq.m instead of 1800 -> fingerprint ${TAMPERED_HASH:0:16}..."
tx 2 verifier1 VerifyDocuments "$APP2" "[\"${TAMPERED_HASH}\"]"
q 2 verifier1 ReadApplication "$APP2" | jq -r '"  status: \(.status)   verification: \(.verification.result)   mismatched: \(.verification.mismatchedDocuments | join(", "))"'
tx 3 collector1 RejectApplication "$APP2" "Document fingerprint mismatch -- referred for investigation"
q 3 collector1 ReadParcel "$SURVEY2" | jq -r '"  parcel \(.surveyNo) released: status \(.status), owner still \(.ownerName)"'

note "4. Reused deed: the first transfer's sale deed is anchored to a different file."
APP3="APP-${RUN}-004"
tx 1 clerk1 SubmitTransferApplication "$APP3" "$SURVEY2" "$BUYER_REF" "Ramesh Kumar" "$SELLER_REF" "3000000"
denied 1 clerk1 AnchorDocument "$APP3" "$(jq -r .contentSha256 <<<"$DEED")" "$(jq -r .docId <<<"$DEED")" SALEDEED "$SRO"

# ===========================================================================
act "Act 7 · Audit -- what anyone on the channel can verify"
note "Public check: is this exact sale deed on the ledger?"
q 3 collector1 VerifyDocument "$(jq -r .contentSha256 <<<"$DEED")" \
  | jq -r '"  genuine deed -> anchored=\(.anchored)  application \(.appId)  by \(.anchoredBy) at \(.anchoredAt)"'
q 3 collector1 VerifyDocument "$TAMPERED_HASH" | jq -r '"  tampered copy -> anchored=\(.anchored)"'

note "Ownership history of ${SURVEY} (every committed version, straight from the ledger):"
q 2 verifier1 ParcelHistory "$SURVEY" \
  | jq -r '.[] | "  \(.timestamp)  tx \(.txId[0:12])...  owner=\(.value.ownerName)  status=\(.value.status)"'

note "Transfer file ${APP} -- who did what:"
q 3 collector1 ReadApplication "$APP" \
  | jq -r '.trail[] | "  \(.at)  \(.org)  \(.by) (\(.role))  \(.action)"'

note "All applications from this run:"
q 3 collector1 ListApplications ALL \
  | jq -r --arg run "$RUN" '.[] | select(.appId | contains($run)) | "  \(.appId)  \(.status)"'

banner "Done -- every step above is a committed, 3-way-endorsed transaction"
