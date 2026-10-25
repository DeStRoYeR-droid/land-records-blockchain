#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# lib/common.sh — shared helpers for the host-side scripts
# (initial_setup.sh, startup.sh, shutdown.sh, demo_transactions.sh).
# Source it; don't execute it.
# ---------------------------------------------------------------------------

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ROOT
export FABRIC_VERSION="${FABRIC_VERSION:-2.5.16}"
export CA_VERSION="${CA_VERSION:-1.5.17}"
COMPOSE_FILE="${ROOT}/network/docker-compose.yaml"
INIT_MARKER="${ROOT}/organizations/.initialised"
DIGILOCKER_URL="${DIGILOCKER_URL:-http://localhost:8000}"

# --- output ---------------------------------------------------------------
if [ -t 1 ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_CYAN=$'\033[36m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_CYAN=""
fi

banner() {
  echo
  echo "${C_BOLD}${C_BLUE}==================================================================${C_RESET}"
  echo "${C_BOLD}${C_BLUE}  $*${C_RESET}"
  echo "${C_BOLD}${C_BLUE}==================================================================${C_RESET}"
}
step()  { echo; echo "${C_BOLD}${C_CYAN}[$1] $2${C_RESET}"; }
info()  { echo "  $*"; }
ok()    { echo "  ${C_GREEN}OK${C_RESET}  $*"; }
warn()  { echo "  ${C_YELLOW}WARN${C_RESET} $*"; }
fail()  { echo "  ${C_RED}FAIL${C_RESET} $*" >&2; exit 1; }
dim()   { echo "${C_DIM}$*${C_RESET}"; }

# --- docker ---------------------------------------------------------------
dc() {
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose -f "$COMPOSE_FILE" "$@"
  else
    fail "Docker Compose not found. Install Docker Desktop (Mac/Windows) or the compose plugin (Linux)."
  fi
}

require_docker() {
  command -v docker >/dev/null 2>&1 || fail "Docker is not installed."
  docker info >/dev/null 2>&1 || fail "Docker is not running. Start Docker Desktop and try again."
  dc version >/dev/null 2>&1 || fail "Docker Compose is not available."
}

# Run a script that lives in network/scripts inside the cli container.
cli_script() {
  local script=$1; shift
  docker exec -i cli bash "/work/network/scripts/${script}" "$@"
}

# Call the chaincode as a certificate holder:  cc invoke|query <org> <user> <Fn> [args]
cc() {
  docker exec cli bash /work/network/scripts/cc.sh "$@"
}

is_initialised() { [ -f "$INIT_MARKER" ]; }

# wait_until "<description>" <timeout-seconds> <command...>
wait_until() {
  local desc=$1 timeout=$2; shift 2
  local waited=0
  until "$@" >/dev/null 2>&1; do
    if [ "$waited" -ge "$timeout" ]; then
      fail "Timed out after ${timeout}s waiting for: ${desc}"
    fi
    sleep 2; waited=$((waited + 2))
  done
}

sha256_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum | awk '{print $1}'
  else shasum -a 256 | awk '{print $1}'
  fi
}

port_in_use() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  else
    (echo >/dev/tcp/127.0.0.1/"$1") >/dev/null 2>&1
  fi
}
