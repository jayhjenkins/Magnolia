#!/usr/bin/env bash
#
# install_granola_sync.sh — install the Granola transcript-sync LaunchAgent.
#
# De-personalized: resolves the repo root and python interpreter at runtime,
# writes a per-user plist under $HOME/Library/LaunchAgents. No identity is
# hardcoded anywhere — the cadence (weekday 9–17 hourly) is built here.
#
# The synced script (granola_sync.py) self-gates on transcript.provider ==
# "granola", so the agent only actually fetches when Granola is the selected
# transcript provider in the Engine tab. Installing the LaunchAgent just arms
# the hourly trigger; it is otherwise a cheap no-op.
#
set -euo pipefail

# 1. Repo root: scripts/ is one level under the repo root.
PM_OS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# 2. Python interpreter (whatever python3 is on PATH).
PYTHON="$(command -v python3)" || { echo "python3 not found on PATH" >&2; exit 1; }

# 3. Generic label — NOT a person's name.
LABEL="com.magnolia.granolasync"

TEMPLATE="$PM_OS_DIR/scripts/templates/transcript-granola-sync.plist.template"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# 4. Build the weekday (1–5) half-hourly :15/:45, 9:15–17:45 StartCalendarInterval block.
INTERVAL_BLOCK=""
for weekday in 1 2 3 4 5; do
    for hour in $(seq 9 17); do
        for minute in 15 45; do
            INTERVAL_BLOCK+="        <dict><key>Weekday</key><integer>${weekday}</integer><key>Hour</key><integer>${hour}</integer><key>Minute</key><integer>${minute}</integer></dict>"$'\n'
        done
    done
done
# Trim the trailing newline so the block sits cleanly inside the array.
INTERVAL_BLOCK="${INTERVAL_BLOCK%$'\n'}"

# 5. Ensure the log directory exists before launchd writes to it.
mkdir -p "$PM_OS_DIR/logs"
mkdir -p "$(dirname "$PLIST")"

# 6. Substitute placeholders. Do the scalar ones with sed (| delimiter so paths
#    with / are safe), then splice the multi-line interval block line-by-line in
#    pure bash so multi-line content survives portably (BSD awk mishandles
#    newlines passed via -v).
rendered="$(
    sed -e "s|__LABEL__|${LABEL}|g" \
        -e "s|__PYTHON__|${PYTHON}|g" \
        -e "s|__PM_OS_DIR__|${PM_OS_DIR}|g" \
        "$TEMPLATE"
)"

# Splice the interval block in place of the __INTERVAL_BLOCK__ marker line.
: > "$PLIST"
while IFS= read -r line; do
    if [[ "$line" == *"__INTERVAL_BLOCK__"* ]]; then
        printf '%s\n' "$INTERVAL_BLOCK" >> "$PLIST"
    else
        printf '%s\n' "$line" >> "$PLIST"
    fi
done <<< "$rendered"

# 7. (Re)load the LaunchAgent.
launchctl unload "$PLIST" 2>/dev/null || true
if ! launchctl load "$PLIST"; then
    echo "launchctl load failed for $PLIST — check 'launchctl list | grep $LABEL' and Console.app for details." >&2
    exit 1
fi

# 8. Confirmation.
cat <<EOF
Installed Granola transcript-sync LaunchAgent.

  Label:    $LABEL
  Plist:    $PLIST
  Runs:     $PYTHON $PM_OS_DIR/scripts/granola_sync.py
  Schedule: weekdays (Mon–Fri), every 30 min at :15 and :45, 9:15–17:45 local
  Logs:     $PM_OS_DIR/logs/granola_sync.log

Note: granola_sync.py self-gates on transcript.provider == "granola". It only
actually syncs when Granola is the selected transcript provider in the Engine
tab; otherwise each run is a logged no-op.

To uninstall:
  launchctl unload "$PLIST" && rm "$PLIST"
EOF
