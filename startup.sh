#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# startup.sh -- start every LandChain process that isn't running, then run the
# automated land-transfer transactions.
#
#   ./startup.sh             start + demo transactions
#   ./startup.sh --no-demo   start only
#   ./startup.sh --no-web    don't start the Next.js dashboards
#
# Ledgers, certificates and keys survive restarts. Run ./initial_setup.sh once first.
# ---------------------------------------------------------------------------
set -eu
. "$(dirname "$0")/lib/common.sh"
ctl start "$@"
