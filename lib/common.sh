#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# lib/common.sh -- shared by the wrapper scripts. Source it; don't execute it.
#
# Finds a Python 3.10+ interpreter, creates .venv on first use and installs
# requirements.txt into it (again only when requirements.txt changes).
# Everything else lives in scripts/landchain_ctl.py.
# Written for bash 3.2 (the macOS default): no associative arrays, no `timeout`.
# ---------------------------------------------------------------------------

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"

# Every usable Python 3.10+ on this machine, best first, one path per line. Homebrew's and
# python.org's are listed explicitly because a shim earlier on PATH (uv, pyenv, conda,
# ~/.local/bin) can be a Python that cannot build a virtualenv.
find_pythons() {
  seen=""
  for candidate in "${PYTHON:-}" \
      /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.11 \
      /usr/local/bin/python3.12 /usr/local/bin/python3.13 /usr/local/bin/python3.11 \
      /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
      /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
      python3.12 python3.13 python3.11 python3.10 python3; do
    [ -n "$candidate" ] || continue
    path=$(command -v "$candidate" 2>/dev/null) || continue
    case " $seen " in *" $path "*) continue ;; esac
    seen="$seen $path"
    if "$path" -c 'import sys, encodings, ensurepip, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
      echo "$path"
    fi
  done
}

make_venv() {
  for base in $(find_pythons); do
    echo "  creating .venv with $base"
    if "$base" -m venv "$VENV" && "$PY" -m pip --version >/dev/null 2>&1; then
      return 0
    fi
    echo "  that Python could not build a working virtualenv; trying the next one"
    rm -rf "$VENV"
  done
  if command -v uv >/dev/null 2>&1; then
    echo "  creating .venv with uv"
    if uv venv --seed --python 3.12 "$VENV" && "$PY" -m pip --version >/dev/null 2>&1; then
      return 0
    fi
    rm -rf "$VENV"
  fi
  echo "  FAIL could not create a virtualenv with any Python 3.10+ on this machine." >&2
  echo "       macOS: brew install python@3.12, then: rm -rf .venv && PYTHON=/opt/homebrew/bin/python3.12 ./initial_setup.sh" >&2
  echo "       Ubuntu: sudo apt install python3.12-venv" >&2
  exit 1
}

ensure_venv() {
  # A .venv made by a Python that has since been upgraded or removed (brew upgrade, pyenv, conda)
  # or copied from another machine cannot start. Detect that and build a fresh one.
  if [ -e "$VENV" ] && ! "$PY" -c 'import encodings, sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    echo "  the existing .venv cannot start (its Python moved or was removed); recreating it"
    rm -rf "$VENV"
  fi
  if [ ! -x "$PY" ]; then
    rm -rf "$VENV"
    make_venv
  fi
  stamp="$VENV/.requirements.sha"
  want=$(cksum < "$ROOT/requirements.txt" | awk '{print $1}')
  have=$(cat "$stamp" 2>/dev/null || echo none)
  if [ "$want" != "$have" ]; then
    echo "  installing Python dependencies into .venv ..."
    "$PY" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
    "$PY" -m pip install -q -r "$ROOT/requirements.txt" || { echo "  FAIL pip install failed" >&2; exit 1; }
    echo "$want" > "$stamp"
  fi
}

ctl() {
  ensure_venv
  exec "$PY" "$ROOT/scripts/landchain_ctl.py" "$@"
}
