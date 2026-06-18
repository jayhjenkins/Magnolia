#!/usr/bin/env bash
# Magnolia one-command installer (macOS / Linux). Fetched via curl and run
# standalone. Installs prerequisites, ensures Claude is present + logged in,
# clones the repo, seeds folder trust, and puts `magnolia` on PATH.
set -euo pipefail

REPO_URL="https://github.com/jayhjenkins/Magnolia.git"
DEST="${MAGNOLIA_DIR:-$HOME/Magnolia}"

say() { printf '\n%s\n' "$1"; }

# 1. Prerequisites via Homebrew
if ! command -v brew >/dev/null 2>&1; then
  say "Homebrew is required. Install it from https://brew.sh and re-run this."
  exit 1
fi
say "Installing prerequisites (git, node, python, pandoc)..."
brew install git node python pandoc
say "Installing qmd (semantic search)..."
npm install -g @tobilu/qmd
say "Installing Python dependencies..."
python3 -m pip install --break-system-packages ruamel.yaml pytest

# 2. Claude CLI: detect-and-direct (never guess an install command)
if ! command -v claude >/dev/null 2>&1; then
  say "Claude Code is required and was not found. Install it from https://claude.com/claude-code, then re-run this installer."
  exit 1
fi

# 3. Login only if not already authenticated
LOGGED_IN="$(python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.claude.json")
try:
    print("yes" if json.load(open(p)).get("oauthAccount") else "no")
except Exception:
    print("no")
PY
)"
if [ "$LOGGED_IN" != "yes" ]; then
  say "Sign in to Claude (a browser will open)..."
  claude login
fi

# 4. Clone (or fast-forward an existing checkout)
if [ ! -d "$DEST/.git" ]; then
  say "Cloning Magnolia into $DEST ..."
  git clone "$REPO_URL" "$DEST"
else
  say "Updating existing Magnolia in $DEST ..."
  git -C "$DEST" pull --ff-only || true
fi

# 5. Seed folder trust + qmd enablement (Inc 1; safe no-op if not logged in)
python3 "$DEST/scripts/trust_seed.py" seed "$DEST" || true

# 6. Put `magnolia` on PATH
mkdir -p "$HOME/.local/bin"
ln -sf "$DEST/bin/magnolia" "$HOME/.local/bin/magnolia"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) : ;;
  *) say "Add this to your shell profile so 'magnolia' is found:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# 7. Done
say "Magnolia is installed. Type:  magnolia   then press Enter."
