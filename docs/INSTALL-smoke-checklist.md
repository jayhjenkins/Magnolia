# Magnolia Installer - Manual Clean-Machine Smoke Checklist

This is the honest verification artifact for the part of the installer that
cannot be automated: real package installs, `claude auth login`, and the browser
OAuth handoff. The shape and syntax of the installers are covered by
`tests/test_installers.py`, `tests/test_install_windows.py` and `bash -n install.sh`;
everything below is a
human, run-it-on-a-real-machine pass.

Run this on a machine that does NOT already have the Magnolia repo. Do a pass
for each OS you support (macOS and Windows).

## Two starting cases

There are two kinds of first run. Note which one you are testing:

- Already a Claude Code user: `~/.claude.json` already has an `oauthAccount`,
  so the installer skips the login step entirely.
- Brand-new user: no Claude login yet. The installer hits exactly ONE
  interactive step - `claude auth login` opens a browser for OAuth. This is the one
  irreducible interactive moment in the whole flow.

---

## macOS / Linux

1. Pre-check: confirm `$HOME/Magnolia` does NOT exist (or set `MAGNOLIA_DIR`
   to a fresh path).
2. Run the one-liner:

   ```
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/jayhjenkins/Magnolia/main/install.sh)"
   ```

   (This form passes the script as an argument, so stdin stays attached to your terminal — the
   same pattern Homebrew's installer uses — which is what lets the `claude auth login` step read input.)

3. Watch each step narrate and complete in order:
   - prerequisites (git, node, python, pandoc) via Homebrew
   - qmd install
   - claude detected (or the install-and-re-run message if absent)
   - login (brand-new user only - browser opens)
   - clone into `$HOME/Magnolia`
   - Python dependencies from `requirements.txt`, then the board-server import
     check (no traceback printed)
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

   On a truly bare machine (no git/node/python yet) the installer should run
   start to finish in ONE window: after winget installs it re-reads PATH from
   the registry, so `npm`, `py`/`python` and `git` resolve without reopening
   the terminal. If any step fails it prints a message and stops WITHOUT
   closing the PowerShell window; re-running is idempotent.

3. Watch each step narrate and complete in order:
   - prerequisites (git, node, python, pandoc) via winget - only the missing ones
   - "Using Python: <path>" - a real `python.exe`, NOT a path under `WindowsApps`
   - python3 shim folder put at the front of PATH
   - qmd install
   - claude detected (or the install-and-re-run message if absent)
   - login (brand-new user only - `claude auth login`, browser opens)
   - clone into `%USERPROFILE%\Magnolia`
   - Python dependencies from `requirements.txt`, then the board-server import
     check (no traceback printed)
   - trust seed (no warning printed)
   - repo `bin` added to PATH
4. Open a NEW PowerShell window (PATH and `PYTHONUTF8` only apply to new
   windows) and confirm prerequisites are present:
   - `qmd --version`
   - `pandoc --version`
   - `git --version`
5. Confirm the Python setup:
   - `python3 --version` prints a Python 3 version (it must NOT open the
     Microsoft Store). `where.exe python3` lists
     `%LOCALAPPDATA%\Magnolia\shims\python3.cmd` FIRST.
   - In Git Bash: `command -v python3` points into the shims folder and
     `python3 --version` works.
   - `echo $env:PYTHONUTF8` prints `1`.
   - Re-run the installer once more: the user PATH still lists the shims folder
     exactly once, at the front.
6. Confirm the repo cloned at `%USERPROFILE%\Magnolia` (or your `MAGNOLIA_DIR`).
7. Confirm trust seeding worked. Run:

   ```
   python3 scripts/trust_seed.py detect
   ```

   Expect `~/.claude.json` to list the repo under `projects` with
   `hasTrustDialogAccepted: true`, and `qmd` present in
   `enabledMcpjsonServers`.
8. Confirm `magnolia` resolves: `where.exe magnolia`.
9. Type `magnolia` and press Enter. The server starts and a browser opens. On a
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

## After onboarding (both OSes)

From the Magnolia folder:

1. **Server import check.** This must print `ok` with no traceback:

   ```
   python3 -c "import sys; sys.path.insert(0, 'scripts'); import task_server; print('ok')"
   ```

   And `magnolia doctor` should show `python_deps` as ok.
2. **Board survives closing the terminal.** With the board open in the
   browser, close the terminal window you ran `magnolia` from (on Windows,
   close the whole window, not just the tab's process). Reload the browser tab:
   the board still loads. Running `magnolia` again reuses the running board
   rather than starting a second one.
3. **Failure is visible.** Server output goes to `logs/task-server.log`
   (Windows: `logs\task-server.log`). Confirm the file exists and has a
   `--- board start ...` line. If `magnolia` ever fails to start the board, it
   prints the last lines of this log - check it shows the real error.
4. **First Granola sync.** If onboarding set Granola as the transcript source,
   run one sync by hand:

   ```
   python3 scripts/transcript_sync.py
   ```

   Expect it to finish without a traceback (no `UnicodeDecodeError` on
   Windows) and new meeting files to appear under `datasets/meetings/` with
   the YAML frontmatter header. A second run should not duplicate them.

## If it fails

Run `magnolia doctor` - it detects and helps remediate a missing or degraded
capability (claude not found, login expired, qmd not enabled, missing Python
packages, etc.). If the board did not start, read `logs/task-server.log` first.
