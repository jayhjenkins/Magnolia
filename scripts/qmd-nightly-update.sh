#!/bin/bash
# ─── macOS only ──────────────────────────────────────────────────────────────
# This script uses macOS LaunchAgents (launchd/launchctl) and Homebrew.
# On Windows, the board auto-starts via Task Scheduler (configured during
# onboarding — see docs/INSTALL-windows.md). Do not run this on Windows.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG="$REPO/logs/qmd-update.log"
echo "=== qmd nightly update: $(date) ===" >> "$LOG"
/opt/homebrew/bin/qmd update >> "$LOG" 2>&1
/opt/homebrew/bin/qmd embed >> "$LOG" 2>&1
echo "=== done: $(date) ===" >> "$LOG"
