#!/bin/sh
# ---------------------------------------------------------------------------
# issue_certs.sh — issue every X.509 certificate the network needs.
#
# Runs INSIDE a hyperledger/fabric-ca container attached to landchain_net,
# with the project root mounted at /work. initial_setup.sh launches it; do
# not run it on the host.
#
# For each organisation it:
#   1. enrolls the CA's bootstrap admin (becomes the org's registrar)
#   2. builds the org MSP with Node OU classification (admin/peer/client/orderer)
#   3. registers + enrolls the node identity (peer or orderer): signing cert + TLS cert
#   4. registers + enrolls the org admin
#   5. registers + enrolls role-bearing users whose certificates carry
#      role=... and dept=... as X.509 attributes -> enforced by the chaincode
#
# POSIX sh only (the image's /bin/sh may be dash or busybox ash).
# ---------------------------------------------------------------------------
set -eu

ORGS_ROOT=/work/organizations
DOMAIN=landchain.example
LOG="${ORGS_ROOT}/issue_certs.log"
: > "$LOG"

say()  { printf '    %s\n' "$*"; }
head1() { printf '\n  == %s ==\n' "$*"; }

# run <cmd...> -- run quietly, but on failure show what went wrong and stop.
run() {
  if ! "$@" >>"$LOG" 2>&1; then
    printf '\n  FAILED: %s\n  ---- last 25 lines of organizations/issue_certs.log ----\n' "$*"
    tail -n 25 "$LOG"
    exit 1
  fi
}

# wait_for_ca <ca-host> <tls-certfile> -- the CA container may still be starting.
wait_for_ca() {
  _i=0
  until fabric-ca-client getcainfo -u "https://$1:7054" --caname "$1" \
        --tls.certfiles "$2" -H "/tmp/cainfo-$1" >/dev/null 2>&1; do
    _i=$((_i + 1))
    if [ "$_i" -ge 30 ]; then
      printf '\n  FAILED: CA %s did not answer on https://%s:7054 within 60s\n' "$1" "$1"
      fabric-ca-client getcainfo -u "https://$1:7054" --caname "$1" --tls.certfiles "$2" -H "/tmp/cainfo-$1" 2>&1 | tail -n 5
      exit 1
    fi
    sleep 2
  done
}

# register <ca-name> <tls-certfile> <id> <secret> <type> [attrs]
# Re-registering an existing identity is fine; any other error stops the run.
register() {
  _ca=$1; _tls=$2; _id=$3; _sec=$4; _type=$5; _attrs=${6:-}
  if [ -n "$_attrs" ]; then
    _out=$(fabric-ca-client register --caname "$_ca" --id.name "$_id" --id.secret "$_sec" \
      --id.type "$_type" --id.attrs "$_attrs" --tls.certfiles "$_tls" 2>&1) && return 0
  else
    _out=$(fabric-ca-client register --caname "$_ca" --id.name "$_id" --id.secret "$_sec" \
      --id.type "$_type" --tls.certfiles "$_tls" 2>&1) && return 0
  fi
  case "$_out" in
    *"already registered"*) say "($_id already registered -- reusing)" ;;
    *) printf '\n  FAILED to register %s with %s:\n%s\n' "$_id" "$_ca" "$_out"; exit 1 ;;
  esac
}

# enroll <ca-name> <ca-host> <tls-certfile> <id> <secret> <msp-dir>
enroll() {
  run fabric-ca-client enroll -u "https://$4:$5@$2:7054" --caname "$1" \
    -M "$6" --tls.certfiles "$3"
}

# enroll_tls <ca-name> <ca-host> <tls-certfile> <id> <secret> <tls-dir> <hostname>
enroll_tls() {
  run fabric-ca-client enroll -u "https://$4:$5@$2:7054" --caname "$1" \
    -M "$6" --enrollment.profile tls \
    --csr.hosts "$7" --csr.hosts localhost \
    --tls.certfiles "$3"
  # Normalise the random file names Fabric CA writes into fixed ones.
  cp "$6"/tlscacerts/* "$6/ca.crt"
  cp "$6"/signcerts/*  "$6/server.crt"
  cp "$6"/keystore/*   "$6/server.key"
}

# write_nodeou_config <msp-dir>
# Classifies identities by the OU that Fabric CA stamps from --id.type.
write_nodeou_config() {
  _cacert=$(ls "$1/cacerts" | head -n 1)
  cat > "$1/config.yaml" <<EOF
NodeOUs:
  Enable: true
  ClientOUIdentifier:
    Certificate: cacerts/${_cacert}
    OrganizationalUnitIdentifier: client
  PeerOUIdentifier:
    Certificate: cacerts/${_cacert}
    OrganizationalUnitIdentifier: peer
  AdminOUIdentifier:
    Certificate: cacerts/${_cacert}
    OrganizationalUnitIdentifier: admin
  OrdererOUIdentifier:
    Certificate: cacerts/${_cacert}
    OrganizationalUnitIdentifier: orderer
EOF
}

# ===========================================================================
# Peer organisations
# ===========================================================================
# issue_peer_org <n> <dept> "<user:role> <user:role> ..."
issue_peer_org() {
  N=$1; DEPT=$2; USERS=$3
  CA="ca-org${N}"
  ORG="org${N}.${DOMAIN}"
  ORGDIR="${ORGS_ROOT}/peerOrganizations/${ORG}"
  TLSCERT="${ORGS_ROOT}/fabric-ca/org${N}/ca-cert.pem"
  PEER="peer0.${ORG}"

  head1 "Org${N}MSP (${DEPT}) -- issued by ${CA}"
  wait_for_ca "$CA" "$TLSCERT"
  mkdir -p "$ORGDIR"
  export FABRIC_CA_CLIENT_HOME="$ORGDIR"

  # 1. CA bootstrap admin -> registrar identity; its MSP becomes the org MSP.
  run fabric-ca-client enroll -u "https://admin:adminpw@${CA}:7054" --caname "$CA" \
    --tls.certfiles "$TLSCERT"
  say "enrolled CA registrar"

  # 2. Org MSP: Node OUs + trust roots.
  write_nodeou_config "$ORGDIR/msp"
  mkdir -p "$ORGDIR/msp/tlscacerts" "$ORGDIR/tlsca" "$ORGDIR/ca"
  cp "$TLSCERT" "$ORGDIR/msp/tlscacerts/ca.crt"
  cp "$TLSCERT" "$ORGDIR/tlsca/tlsca.${ORG}-cert.pem"
  cp "$TLSCERT" "$ORGDIR/ca/ca.${ORG}-cert.pem"
  say "built org MSP with Node OUs"

  # 3. Peer node: signing identity + TLS identity.
  register "$CA" "$TLSCERT" peer0 peer0pw peer
  enroll     "$CA" "$CA" "$TLSCERT" peer0 peer0pw "$ORGDIR/peers/${PEER}/msp"
  cp "$ORGDIR/msp/config.yaml" "$ORGDIR/peers/${PEER}/msp/config.yaml"
  enroll_tls "$CA" "$CA" "$TLSCERT" peer0 peer0pw "$ORGDIR/peers/${PEER}/tls" "$PEER"
  say "peer0  -> signing cert + TLS cert (SAN ${PEER}, localhost)"

  # 4. Org admin (OU=admin) -- used for channel join and chaincode lifecycle.
  register "$CA" "$TLSCERT" "org${N}admin" "org${N}adminpw" admin
  enroll "$CA" "$CA" "$TLSCERT" "org${N}admin" "org${N}adminpw" "$ORGDIR/users/Admin@${ORG}/msp"
  cp "$ORGDIR/msp/config.yaml" "$ORGDIR/users/Admin@${ORG}/msp/config.yaml"
  say "Admin@${ORG}"

  # 5. Role-bearing users. The ':ecert' suffix embeds the attribute in the
  #    X.509 certificate itself, so the chaincode can read it on every call.
  for pair in $USERS; do
    U=${pair%%:*}; R=${pair#*:}
    register "$CA" "$TLSCERT" "$U" "${U}pw" client "role=${R}:ecert,dept=${DEPT}:ecert"
    enroll "$CA" "$CA" "$TLSCERT" "$U" "${U}pw" "$ORGDIR/users/${U}@${ORG}/msp"
    cp "$ORGDIR/msp/config.yaml" "$ORGDIR/users/${U}@${ORG}/msp/config.yaml"
    say "${U}  role=${R}  dept=${DEPT}"
  done
}

# ===========================================================================
# Orderer organisation
# ===========================================================================
issue_orderer_org() {
  CA="ca-orderer"
  ORGDIR="${ORGS_ROOT}/ordererOrganizations/${DOMAIN}"
  TLSCERT="${ORGS_ROOT}/fabric-ca/ordererOrg/ca-cert.pem"

  head1 "OrdererMSP -- issued by ${CA}"
  wait_for_ca "$CA" "$TLSCERT"
  mkdir -p "$ORGDIR"
  export FABRIC_CA_CLIENT_HOME="$ORGDIR"

  run fabric-ca-client enroll -u "https://admin:adminpw@${CA}:7054" --caname "$CA" \
    --tls.certfiles "$TLSCERT"
  say "enrolled CA registrar"

  write_nodeou_config "$ORGDIR/msp"
  mkdir -p "$ORGDIR/msp/tlscacerts" "$ORGDIR/tlsca"
  cp "$TLSCERT" "$ORGDIR/msp/tlscacerts/tlsca.${DOMAIN}-cert.pem"
  cp "$TLSCERT" "$ORGDIR/tlsca/tlsca.${DOMAIN}-cert.pem"
  say "built org MSP with Node OUs"

  for i in 1 2 3; do
    NODE="orderer${i}.${DOMAIN}"
    register "$CA" "$TLSCERT" "orderer${i}" "orderer${i}pw" orderer
    enroll     "$CA" "$CA" "$TLSCERT" "orderer${i}" "orderer${i}pw" "$ORGDIR/orderers/${NODE}/msp"
    cp "$ORGDIR/msp/config.yaml" "$ORGDIR/orderers/${NODE}/msp/config.yaml"
    enroll_tls "$CA" "$CA" "$TLSCERT" "orderer${i}" "orderer${i}pw" "$ORGDIR/orderers/${NODE}/tls" "$NODE"
    mkdir -p "$ORGDIR/orderers/${NODE}/msp/tlscacerts"
    cp "$ORGDIR/orderers/${NODE}/tls/ca.crt" "$ORGDIR/orderers/${NODE}/msp/tlscacerts/tlsca.${DOMAIN}-cert.pem"
    say "orderer${i} -> signing cert + TLS cert (SAN ${NODE}, localhost)"
  done

  register "$CA" "$TLSCERT" ordereradmin ordereradminpw admin
  enroll "$CA" "$CA" "$TLSCERT" ordereradmin ordereradminpw "$ORGDIR/users/Admin@${DOMAIN}/msp"
  cp "$ORGDIR/msp/config.yaml" "$ORGDIR/users/Admin@${DOMAIN}/msp/config.yaml"
  say "Admin@${DOMAIN}"
}

# ===========================================================================
issue_orderer_org
issue_peer_org 1 registration "registrar1:registrar clerk1:clerk"
issue_peer_org 2 revenue      "verifier1:verifier surveyor1:surveyor"
issue_peer_org 3 collectorate "collector1:collector"

printf '\n  All certificates issued.\n'
