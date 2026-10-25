#!/usr/bin/env bash
# demo_transactions.sh -- the automated land transfer, against a running network.
#   DEMO_PAUSE=2 ./demo_transactions.sh   pause between acts when presenting live
set -eu
. "$(dirname "$0")/lib/common.sh"
ensure_venv
cd "$ROOT"
PYTHONPATH="$ROOT" exec "$PY" scripts/demo_transactions.py "$@"
