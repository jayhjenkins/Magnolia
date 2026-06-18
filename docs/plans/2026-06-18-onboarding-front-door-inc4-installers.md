# Onboarding Front Door — Increment 4: the curl installers Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** The `curl`-able bootstrap that turns a bare machine into a ready-to-launch Magnolia: install prerequisites, ensure `claude` is present + logged in, clone the repo, seed folder trust (Inc 1), and put `magnolia` (Inc 2) on PATH — ending with "type `magnolia`." Two native per-OS scripts (`install.sh`, `install.ps1`) plus a `magnolia` shim, with shape/syntax tests and a manual clean-machine smoke checklist.

**Architecture:** The installers are standalone native bootstrap scripts (the one legitimate home for OS-specific package-manager commands — the portability gate scans only `scripts/**/*.py` + `ui/**/*.js`, NOT shell/PS, so they are not gated). They reuse already-tested Python: `scripts/trust_seed.py seed <path>` for the trust step (Inc 1). `magnolia` reaches the launcher (`scripts/magnolia.py`, Inc 2) via a thin shim in `bin/`. The shell scripts are intentionally thin — anything with real logic lives in tested Python — so the un-auto-testable surface (real package installs, `claude login`, browser OAuth) is minimized and covered by a documented manual smoke checklist, per the design's honest verification limit.

**Tech Stack:** bash, PowerShell, a tiny Python shape-test (`tests/test_installers.py`).

**Increment roadmap:** Inc 1 ✅ (trust_seed), Inc 2 ✅ (magnolia launcher). **Inc 4 = this** (built before Inc 3 to avoid touching `task_server.py` until after the Cadence merge). Inc 3 (first-run gate + onboarding UI + onboard_runner) lands last, after Cadence merges to main. All stacked on `feat/onboarding-front-door` / PR #43.

**Decisions baked in:**
- `claude` binary: **detect-and-direct** (if absent, print the official Claude Code install URL and exit cleanly) — never guess an install command. `claude login` is the one irreducible interactive step.
- Repo URL: `https://github.com/jayhjenkins/Magnolia.git` (matches `docs/INSTALL-macos.md`).
- Clone dest: `$MAGNOLIA_DIR` env override, else `$HOME/Magnolia`.
- Trust seed runs AFTER login + clone (so `~/.claude.json` and the repo both exist).
- Runtime/user-facing text is ASCII (hyphen, not em-dash) per invariant #8.

**Out of scope (deferred):** Rewriting `docs/INSTALL-macos.md` / `INSTALL-windows.md` to the one-command flow — the end-to-end experience isn't complete until Inc 3's in-UI onboarding exists (until then, after `magnolia` the board opens but onboarding is still the conversational `onboard me`). The smoke checklist notes this.

---

## Task 1: the `magnolia` shims (`bin/magnolia`, `bin/magnolia.cmd`)

**Files:**
- Create: `bin/magnolia` (unix), `bin/magnolia.cmd` (windows)
- Test: `tests/test_installers.py`

**Step 1: Write the failing tests**

```python
import os
import stat

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_unix_shim_exists_executable_and_targets_launcher():
    p = os.path.join(ROOT, "bin", "magnolia")
    assert os.path.isfile(p)
    assert os.stat(p).st_mode & stat.S_IXUSR        # executable
    body = open(p, encoding="utf-8").read()
    assert body.startswith("#!")                     # has a shebang
    assert "scripts/magnolia.py" in body             # routes to the launcher
    assert "readlink" in body                        # resolves its own symlink to find the repo


def test_windows_shim_targets_launcher():
    p = os.path.join(ROOT, "bin", "magnolia.cmd")
    assert os.path.isfile(p)
    body = open(p, encoding="utf-8").read()
    assert "magnolia.py" in body
    assert "%*" in body                              # forwards args
```

**Step 2: Run to verify fail**

Run: `cd /Users/jayjenkins/dev/pm-os-onboarding && python3 -m pytest tests/test_installers.py -v`
Expected: FAIL — files missing.

**Step 3: Create the shims**

`bin/magnolia` (portable symlink-resolution idiom — plain `readlink`, works on macOS + Linux):
```bash
#!/usr/bin/env bash
# magnolia - launch the Magnolia board. Resolves its own (possibly symlinked)
# location to find the repo, then hands off to the Python launcher.
set -euo pipefail
src="${BASH_SOURCE[0]}"
while [ -h "$src" ]; do
  dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  [[ $src != /* ]] && src="$dir/$src"
done
dir="$(cd -P "$(dirname "$src")" && pwd)"
exec python3 "$dir/../scripts/magnolia.py" "$@"
```

`bin/magnolia.cmd` (the installer adds `<repo>\bin` to PATH, so `%~dp0` resolves to the repo's bin in place):
```bat
@echo off
python "%~dp0..\scripts\magnolia.py" %*
```

Then make the unix shim executable:
```bash
chmod +x bin/magnolia
```

**Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_installers.py -v`
Expected: PASS (2).

**Step 5: Commit**

```bash
git add bin/magnolia bin/magnolia.cmd tests/test_installers.py
git commit -m "feat(onboarding): magnolia shims (unix + windows) routing to the launcher (Inc 4)"
```

---

## Task 2: `install.sh` (macOS / Linux bootstrap)

**Files:**
- Create: `install.sh` (repo root)
- Test: `tests/test_installers.py`

**Step 1: Add the failing tests** (syntax parse + required-step shape; `bash -n` skips gracefully if bash is unavailable)

```python
import shutil
import subprocess


def test_install_sh_parses_clean():
    p = os.path.join(ROOT, "install.sh")
    assert os.path.isfile(p)
    bash = shutil.which("bash")
    if not bash:
        return  # environment without bash; shape checks below still apply
    r = subprocess.run([bash, "-n", p], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_install_sh_has_required_steps():
    body = open(os.path.join(ROOT, "install.sh"), encoding="utf-8").read()
    assert "brew install" in body                       # prerequisites
    assert "@tobilu/qmd" in body                         # qmd (exact package)
    assert "ruamel.yaml" in body                         # python deps
    assert "command -v claude" in body                   # detect-and-direct for claude
    assert "git clone" in body
    assert "scripts/trust_seed.py" in body and "seed" in body   # Inc 1 trust seed
    assert ".local/bin/magnolia" in body                 # magnolia on PATH
    assert "magnolia" in body.lower()                    # the closing instruction
```

**Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_installers.py -k install_sh -v`
Expected: FAIL — `install.sh` missing.

**Step 3: Create `install.sh`**

```bash
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
```

**Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_installers.py -k install_sh -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add install.sh tests/test_installers.py
git commit -m "feat(onboarding): install.sh macOS/Linux curl bootstrap (Inc 4)"
```

---

## Task 3: `install.ps1` (Windows bootstrap)

**Files:**
- Create: `install.ps1` (repo root)
- Test: `tests/test_installers.py`

**Step 1: Add the failing test** (shape only; PowerShell parse is optional — pwsh is usually absent on the dev/CI box, so do not require it)

```python
def test_install_ps1_has_required_steps():
    p = os.path.join(ROOT, "install.ps1")
    assert os.path.isfile(p)
    body = open(p, encoding="utf-8").read()
    assert "winget install" in body                      # windows prerequisites
    assert "@tobilu/qmd" in body
    assert "git clone" in body
    assert "scripts/trust_seed.py" in body or "scripts\\trust_seed.py" in body
    assert "claude" in body                               # detect-and-direct
    assert "bin" in body                                  # adds repo bin to PATH
    assert "magnolia" in body.lower()
```

**Step 2: Run to verify fail**

Run: `python3 -m pytest tests/test_installers.py -k install_ps1 -v`
Expected: FAIL.

**Step 3: Create `install.ps1`**

```powershell
# Magnolia one-command installer (Windows). Fetched via curl/irm and run
# standalone. Mirrors install.sh: prerequisites, Claude present + logged in,
# clone, trust seed, magnolia on PATH. Native PowerShell - no WSL.
$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/jayhjenkins/Magnolia.git"
$Dest = if ($env:MAGNOLIA_DIR) { $env:MAGNOLIA_DIR } else { Join-Path $HOME "Magnolia" }

function Say($m) { Write-Host "`n$m" }

# 1. Prerequisites via winget
Say "Installing prerequisites (git, node, python, pandoc)..."
winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
winget install --id OpenJS.NodeJS -e
winget install --id Python.Python.3.12 -e
winget install --id JohnMacFarlane.Pandoc -e
Say "Installing qmd (semantic search)..."
npm install -g @tobilu/qmd
Say "Installing Python dependencies..."
python -m pip install ruamel.yaml pytest

# 2. Claude CLI: detect-and-direct
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Say "Claude Code is required and was not found. Install it from https://claude.com/claude-code, then re-run this installer."
    exit 1
}

# 3. Login only if not already authenticated
$cfg = Join-Path $HOME ".claude.json"
$loggedIn = $false
if (Test-Path $cfg) {
    try { if ((Get-Content $cfg -Raw | ConvertFrom-Json).oauthAccount) { $loggedIn = $true } } catch {}
}
if (-not $loggedIn) { Say "Sign in to Claude (a browser will open)..."; claude login }

# 4. Clone (or fast-forward)
if (-not (Test-Path (Join-Path $Dest ".git"))) {
    Say "Cloning Magnolia into $Dest ..."
    git clone $RepoUrl $Dest
} else {
    Say "Updating existing Magnolia in $Dest ..."
    git -C $Dest pull --ff-only
}

# 5. Seed folder trust + qmd enablement (Inc 1)
python (Join-Path $Dest "scripts/trust_seed.py") seed $Dest

# 6. Put magnolia on PATH (add repo bin so bin\magnolia.cmd resolves in place)
$bin = Join-Path $Dest "bin"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$bin*") {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$bin", "User")
    Say "Added $bin to your PATH (open a new terminal to pick it up)."
}

# 7. Done
Say "Magnolia is installed. Type:  magnolia   then press Enter."
```

**Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_installers.py -k install_ps1 -v`
Expected: PASS.

**Step 5: Commit**

```bash
git add install.ps1 tests/test_installers.py
git commit -m "feat(onboarding): install.ps1 Windows curl bootstrap (Inc 4)"
```

---

## Task 4: the manual clean-machine smoke checklist

**Files:**
- Create: `docs/INSTALL-smoke-checklist.md`

**Step 1:** Write the checklist (no test — this IS the honest verification artifact for the part that can't be automated). Cover, as a numbered manual checklist for BOTH macOS and Windows:
- Pre: a machine WITHOUT the repo. Note the two cases (already-a-Claude-user vs brand-new) and that a brand-new user hits one `claude login` browser step.
- Run the curl/irm one-liner; observe each step narrates and completes.
- Confirm: prerequisites present (`qmd --version`, `pandoc --version`, `git --version`); repo cloned at `$HOME/Magnolia`; `~/.claude.json` has the repo under `projects` with `hasTrustDialogAccepted: true` and `qmd` in `enabledMcpjsonServers` (run `python3 scripts/trust_seed.py detect`); `magnolia` resolves on PATH.
- Type `magnolia`: server starts, browser opens to the board.
- KNOWN GAP until Inc 3: there is no in-UI onboarding yet, so after `magnolia` the board opens but first-run onboarding is still the conversational `onboard me`. Note this explicitly.
- A short "if it fails" row: `magnolia doctor`.

Keep it ASCII, plain language.

**Step 2: Commit**

```bash
git add docs/INSTALL-smoke-checklist.md
git commit -m "docs(onboarding): manual clean-machine smoke checklist for the installers (Inc 4)"
```

---

## Task 5: Run all five gates green, then push

**Step 1: Gates**

```bash
cd /Users/jayjenkins/dev/pm-os-onboarding
python3 -m pytest -q
python3 scripts/card_schema.py        # -> registry.json OK
python3 -m pytest tests/test_engine_no_jay.py -q
python3 scripts/portability_gate.py   # -> portability OK  (shell/PS not scanned)
python3 scripts/program_schema.py     # -> programtypes OK
```
Expected: all green. The installers are shell/PS (not scanned by portability or de-personalization gates); `tests/test_installers.py` is pure stdlib.

**Step 2:** Do NOT open a new PR. Stack onto PR #43:
```bash
git push
```

---

## Notes for the executor

- Worktree `/Users/jayjenkins/dev/pm-os-onboarding`, branch `feat/onboarding-front-door`. NEVER `git checkout` another branch.
- The installers are **Tier-1 from the engine's view** (no engine-mediated external write). They DO install software and run `claude login` on the user's machine, but that is the user's own bootstrap, run with their consent via the curl one-liner — not an engine adapter publish.
- Do not try to actually run `install.sh`/`install.ps1` end-to-end here (they install software / clone / login) — verification is `bash -n`, the shape tests, and the manual checklist. That honesty is the point.
- If the full suite dirties `profile.example/capabilities.json`, restore it (the fix is already on-branch; it should stay clean) and do not stage it.
- ASCII runtime/user-facing text only (hyphen, not em-dash).
