#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# shutdown.sh -- stop LandChain.
#
#   ./shutdown.sh            stop every process gracefully; data is kept, so
#                            ./startup.sh resumes exactly where you left off
#   ./shutdown.sh --clean    delete EVERYTHING generated: ledgers, certificates,
#                            keys, logs. You will need ./initial_setup.sh again.
#   --yes                    skip the confirmation prompt for --clean
# ---------------------------------------------------------------------------
set -eu
. "$(dirname "$0")/lib/common.sh"
clean=0; yes=""
for arg in "$@"; do
  case "$arg" in
    --clean) clean=1 ;;
    --yes|-y) yes="--yes" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 1 ;;
  esac
done
if [ "$clean" -eq 1 ]; then
  if [ -n "$yes" ]; then ctl clean --yes; else ctl clean; fi
else
  ctl stop
fi
