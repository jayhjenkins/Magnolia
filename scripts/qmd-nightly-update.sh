#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG="$REPO/logs/qmd-update.log"
echo "=== qmd nightly update: $(date) ===" >> "$LOG"
# qmd indexes markdown only; refresh the plain-text mirror of HTML artifacts first.
# A mirror failure must not block the markdown index update.
python3 "$REPO/scripts/html_text_mirror.py" >> "$LOG" 2>&1 || echo "html_text_mirror failed" >> "$LOG"
/opt/homebrew/bin/qmd update >> "$LOG" 2>&1
/opt/homebrew/bin/qmd embed >> "$LOG" 2>&1
echo "=== done: $(date) ===" >> "$LOG"
