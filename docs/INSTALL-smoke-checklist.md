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

   (`detect` takes no repo path because it reads your GLOBAL `~/.claude.json` -
   where folder trust was written.)

   Expect `~/.claude.json` to list the repo under `projects` with
   `hasTrustDialogAccepted: true`, and `qmd` present in
   `enabledMcpjsonServers`.
7. Confirm `magnolia` resolves on PATH: `which magnolia` (open a new terminal
   first if the installer told you to add `~/.local/bin` to PATH).
8. Type `magnolia` and press Enter. The server starts and a browser opens. On a
   fresh setup (no live `profile/`) it lands on the **onboarding room**, not the
   board: a "Welcome to Magnolia" screen with an **Onboard me** button. Click it,
   walk the guided setup, and confirm that on completion the room runs its reveal
   and hands off to the board. (Re-running `magnolia` afterward opens straight to
   the board.)

---

## Windows

1. Pre-check: confirm `%USERPROFILE%\Magnolia` does NOT exist (or set
   `MAGNOLIA_DIR` to a fresh path).
2. Run the one-liner in PowerShell:

   ```
   irm https://raw.githubusercontent.com/jayhjenkins/Magnolia/main/install.ps1 | iex
   ```

   Note: winget does not refresh the CURRENT shell's PATH, so on a truly bare
   machine the immediately-following `npm install -g @tobilu/qmd` (and `git`)
   may not be found in the same session. If the installer errors there, open a
   NEW terminal and re-run it - the installer is idempotent. (This mirrors the
   "hot-swap" PATH gotcha in INSTALL-windows.md.)

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
8. Type `magnolia` and press Enter. The server starts and a browser opens. On a
   fresh setup (no live `profile/`) it lands on the **onboarding room**, not the
   board: a "Welcome to Magnolia" screen with an **Onboard me** button. Click it,
   walk the guided setup, and confirm that on completion the room runs its reveal
   and hands off to the board. (Re-running `magnolia` afterward opens straight to
   the board.)

---

## First-run onboarding (the in-UI flow)

The first-run gate and the in-UI onboarding room are live. After `magnolia`
opens the browser on a fresh setup, onboarding runs **inside the board** (a
headless Claude session driving the `meta-onboard` skill), not as a separate
`onboard me` prompt. Verify:

- A fresh setup serves the onboarding room at `/` (the gate), not the board.
- **Onboard me** streams the concierge conversation; browser sign-in windows for
  connectors (Granola / M365 / qmd) pop OUTSIDE the chat and are narrated in
  plain language.
- On completion the room reveals the board and re-running `magnolia` goes
  straight to the board (the `onboarded` marker is set, so the gate no longer
  fires).

## If it fails

Run `magnolia doctor` - it detects and helps remediate a missing or degraded
capability (claude not found, login expired, qmd not enabled, etc.).
