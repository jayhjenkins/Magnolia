#!/usr/bin/env bash
# task.sh — PM-OS Task Management CLI wrapper
# Thin bash wrapper that calls python3 scripts/task_cli.py
#
# Usage:
#   ./scripts/task.sh add "Title" --queue human --priority high
#   ./scripts/task.sh list --queue agent --json
#   ./scripts/task.sh show TASK-0001
#   ./scripts/task.sh update TASK-0001 --status in-progress
#   ./scripts/task.sh done TASK-0001
#   ./scripts/task.sh agent:start TASK-0001
#   ./scripts/task.sh agent:complete TASK-0001 --output "path/to/artifact"
#   ./scripts/task.sh agent:fail TASK-0001 --error "reason"
#   ./scripts/task.sh agent:ask TASK-0001 "question"
#   ./scripts/task.sh inbox

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PM_OS_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PM_OS_DIR"
# Pick a real interpreter. On Windows (Git Bash) `python3`/`python` are often the
# Microsoft Store stub in .../WindowsApps, which opens the Store instead of
# running anything. `command -v` only reports the FIRST match, so walk every PATH
# entry, skip WindowsApps, and fall back to the `py` launcher.
PYTHON=""
OLD_IFS="$IFS"; IFS=':'
for cand in python3 python; do
  for d in $PATH; do
    case "$d" in
      ""|*/[Ww]indows[Aa]pps|*/[Ww]indows[Aa]pps/*) continue ;;
    esac
    for f in "$d/$cand" "$d/$cand.exe"; do
      if [ -f "$f" ] && [ -x "$f" ]; then PYTHON="$f"; break 3; fi
    done
  done
done
IFS="$OLD_IFS"
if [ -z "$PYTHON" ] && command -v py >/dev/null 2>&1; then
  exec py -3 "$SCRIPT_DIR/task_cli.py" "$@"
fi
if [ -z "$PYTHON" ]; then
  echo "task.sh: no python3/python found on PATH (Windows: re-run install.ps1)" >&2
  exit 127
fi
exec "$PYTHON" "$SCRIPT_DIR/task_cli.py" "$@"
