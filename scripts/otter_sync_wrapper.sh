#!/usr/bin/env bash
# Wrapper for launchd-scheduled Otter sync.
# Detects a broken venv (e.g. after brew upgrades Python) and rebuilds it
# before running the sync script.

set -euo pipefail

MAGNOLIA_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$MAGNOLIA_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python3"
SYNC_SCRIPT="$MAGNOLIA_DIR/scripts/otter_sync.py"
LOG="$MAGNOLIA_DIR/logs/otter_sync.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  INFO  [wrapper] $*" >> "$LOG"; }

if "$VENV_PYTHON" -c "import sys" 2>/dev/null; then
    exec "$VENV_PYTHON" "$SYNC_SCRIPT"
fi

# Venv is broken — rebuild
log "Venv broken (likely Python upgrade). Rebuilding..."

SYSTEM_PYTHON="$(command -v python3)"
if [ -z "$SYSTEM_PYTHON" ]; then
    log "ERROR: No system python3 found on PATH"
    exit 1
fi

PYVER="$("$SYSTEM_PYTHON" --version 2>&1)"
log "Rebuilding venv with $SYSTEM_PYTHON ($PYVER)"

"$SYSTEM_PYTHON" -m venv --clear "$VENV_DIR" 2>>"$LOG"
"$VENV_PYTHON" -m pip install --upgrade pip -q 2>>"$LOG"
"$VENV_PYTHON" -m pip install -q \
    -r "$MAGNOLIA_DIR/requirements-dev.txt" \
    -r "$MAGNOLIA_DIR/requirements-transcript.txt" \
    2>>"$LOG"

log "Venv rebuilt successfully. Running sync."
exec "$VENV_PYTHON" "$SYNC_SCRIPT"
