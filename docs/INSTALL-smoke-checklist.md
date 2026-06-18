# Magnolia Installer - Manual Clean-Machine Smoke Checklist

This is the honest verification artifact for the part of the installer that
cannot be automated: real package installs, `claude login`, and the browser
OAuth handoff. The shape and syntax of the installers are covered by
`tests/test_installers.py` and `bash -n install.sh`; everything below is a
human, run-it-on-a-real-machine pass.

Run this on a machine that does NOT already have the Magnolia repo. Do a pass
for each OS you support (macOS and Windows).

## Two starting cases

There are two kinds of first run. Note which one you are testing:

- Already a Claude Code user: `~/.claude.json` already has an `oauthAccount`,
  so the installer skips the login step entirely.
- Brand-new user: no Claude login yet. The installer hits exactly ONE
  interactive step - `claude login` opens a browser for OAuth. This is the one
  irreducible interactive moment in the whole flow.

---

## macOS / Linux

1. Pre-check: confirm `$HOME/Magnolia` does NOT exist (or set `MAGNOLIA_DIR`
   to a fresh path).
2. Run the one-liner:

   ```
   curl -fsSL https://raw.githubusercontent.com/jayhjenkins/Magnolia/main/install.sh | bash
   ```

3. Watch each step narrate and complete in order:
   - prerequisites (git, node, python, pandoc) via Homebrew
   - qmd install
   - Python dependencies
   - claude detected (or the install-and-re-run message if absent)
   - login (brand-new user only - browser opens)
   - clone into `$HOME/Magnolia`
   - trust seed
   - magnolia placed on PATH
4. Confirm prerequisites are present:
   - `qmd --version`
   - `pandoc --version`
   - `git --version`
5. Confirm the repo cloned at `$HOME/Magnolia` (or your `MAGNOLIA_DIR`).
6. Confirm trust seeding worked. Run:

   ```
   python3 scripts/trust_seed.py detect
   ```

   Expect `~/.claude.json` to list the repo under `projects` with
   `hasTrustDialogAccepted: true`, and `qmd` present in
   `enabledMcpjsonServers`.
7. Confirm `magnolia` resolves on PATH: `which magnolia` (open a new terminal
   first if the installer told you to add `~/.local/bin` to PATH).
8. Type `magnolia` and press Enter. The server starts and a browser opens to
   the board.

---

## Windows

1. Pre-check: confirm `%USERPROFILE%\Magnolia` does NOT exist (or set
   `MAGNOLIA_DIR` to a fresh path).
2. Run the one-liner in PowerShell:

   ```
   irm https://raw.githubusercontent.com/jayhjenkins/Magnolia/main/install.ps1 | iex
   ```

3. Watch each step narrate and complete in order:
   - prerequisites (git, node, python, pandoc) via winget
   - qmd install
   - Python dependencies
   - claude detected (or the install-and-re-run message if absent)
   - login (brand-new user only - browser opens)
   - clone into `%USERPROFILE%\Magnolia`
   - trust seed
   - repo `bin` added to PATH
4. Confirm prerequisites are present:
   - `qmd --version`
   - `pandoc --version`
   - `git --version`
5. Confirm the repo cloned at `%USERPROFILE%\Magnolia` (or your `MAGNOLIA_DIR`).
6. Confirm trust seeding worked. Run:

   ```
   python scripts/trust_seed.py detect
   ```

   Expect `~/.claude.json` to list the repo under `projects` with
   `hasTrustDialogAccepted: true`, and `qmd` present in
   `enabledMcpjsonServers`.
7. Open a NEW terminal (so the PATH change is picked up) and confirm
   `magnolia` resolves: `where magnolia`.
8. Type `magnolia` and press Enter. The server starts and a browser opens to
   the board.

---

## KNOWN GAP (until Inc 3)

There is no in-UI onboarding yet. After `magnolia` opens the board, first-run
onboarding is still the conversational `onboard me` flow - you start it
yourself by typing `onboard me` to Magnolia. Inc 3 adds the first-run gate and
the in-UI onboarding screen; until then the smoke pass ends at "board opens."

## If it fails

Run `magnolia doctor` - it detects and helps remediate a missing or degraded
capability (claude not found, login expired, qmd not enabled, etc.).
