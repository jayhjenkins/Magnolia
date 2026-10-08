# Installing Magnolia — Windows

One command installs everything; then you type `magnolia` and a browser opens into guided setup.

> **Magnolia runs natively on Windows.** You do **not** need WSL, Ubuntu, or any Linux setup —
> just the native Windows tools below (winget + PowerShell + Python). If Claude ever suggests
> installing WSL/Ubuntu to get Magnolia working, that's a mistake: decline it and report the
> underlying error instead.

## The shape (one command, then `magnolia`)
1. **Install Claude Code** if you don't have it yet (one time): https://claude.com/claude-code
2. **Run the installer** (below). It installs prerequisites, signs you into Claude if needed,
   clones the repo, seeds folder trust, and puts `magnolia` on your PATH.
3. **Type `magnolia`.** The board starts and your browser opens — into the guided onboarding room
   on a fresh setup, or straight to your board once you're set up.

No restart, no second prompt. The installer does the PATH hand-off that used to require quitting
and reopening Claude Code, and onboarding now runs inside the board.

---

## Prerequisites
- **winget** — ships with current Windows; the installer uses it for git/node/python/pandoc.
- **Git for Windows** — provides the Git Bash shell Magnolia and Claude Code rely on (the
  installer installs it via winget if missing).
- **Claude Code** — required. The installer detects it; if it's missing, it stops and points you
  to https://claude.com/claude-code. Install it, then re-run the installer.

---

## ⚠️ Where Magnolia lands (and how to change it)
By default the installer clones to **`%USERPROFILE%\Magnolia`**. That's fine for most people.

**If you already use Claude Code and want Magnolia to inherit your existing setup**, point it at
the workspace where Claude Code already works for you — the folder where your corporate
integrations (Granola, Microsoft 365, Jira, Pendo, Databricks) and personal skills already show
up. Set `MAGNOLIA_DIR` before running, in the same PowerShell window:

```
$env:MAGNOLIA_DIR = "$HOME\dev\Magnolia"
```

Why it matters: those integrations are **claude.ai account connectors**. They follow you
everywhere, but a brand-new, never-opened folder can come up *untrusted* with connectors not yet
enabled — making Magnolia look like it can't see integrations you actually have. The installer
seeds folder trust + qmd to avoid that, and landing Magnolia next to your existing Claude Code
work lets it inherit your skills too. You don't need to re-architect anything.

---

## Install

Run this in PowerShell:

```
irm https://raw.githubusercontent.com/jayhjenkins/Magnolia/main/install.ps1 | iex
```

(Windows is unaffected by the stdin footgun that the macOS/Linux installer guards against:
PowerShell's `iex` runs the script in-session rather than reading it from a pipe, so the
sign-in step reads your terminal as-is.) It will, in order:
- install whichever prerequisites are missing via winget (git, node, python, pandoc), then
  re-read PATH from the registry so the freshly installed tools work in the same window
- find your **real** Python (the `py -3` launcher or the installed `python.exe`) — never the
  Microsoft Store "python" stub in `...\WindowsApps`, which only opens the Store
- create a small **`python3` shim** folder (`%LOCALAPPDATA%\Magnolia\shims`) and put it at the
  front of your user PATH (details below)
- set **`PYTHONUTF8=1`** for your user account (details below)
- install **qmd** (semantic search)
- confirm Claude Code is present (or stop and tell you to install it)
- sign you into Claude **only if you aren't already** (`claude auth login` — a browser opens; this
  is the one interactive moment for a brand-new user)
- clone Magnolia to `%USERPROFILE%\Magnolia` (or your `MAGNOLIA_DIR`)
- install the Python dependencies from `requirements.txt` and check that the board server imports
  cleanly (if pip fails, it stops with a clear message instead of failing later)
- seed folder trust + qmd enablement
- add the repo's `bin` folder to your user PATH

If a step fails, the installer prints what went wrong and stops without closing your PowerShell
window. Fix the cause and run the one-liner again — it's idempotent and picks up where it left off.

### What the installer changes on your account (and why)
- **`python3` shim.** Magnolia's skills, workers and hooks call `python3`. On Windows that name is
  usually missing or the Store stub. The installer writes `python3.cmd` (for PowerShell/cmd) and an
  extensionless `python3` script (for Git Bash, which Claude Code uses on Windows) into
  `%LOCALAPPDATA%\Magnolia\shims`; both run your real Python by its full path. That folder goes
  first on your **user** PATH so it wins over `...\WindowsApps`. If plain `python` is also the
  Store stub, a `python` shim is added the same way; a working `python` is left alone. Re-running
  the installer rewrites the shims (handy if you reinstall or move Python).
- **`PYTHONUTF8=1`.** Windows' default console encoding (cp1252) garbles or crashes on Claude's
  UTF-8 output. The installer sets `PYTHONUTF8=1` as a user environment variable.
- Both take effect in **new** terminal windows — open a fresh one after installing.

---

## Start it

Open a **new** terminal (so the PATH change is picked up), then:

```
magnolia
```

The board starts and your browser opens. On a fresh setup it lands on the **onboarding room** —
click **Onboard me** and the concierge walks you through identity, integrations, and a quick
capability check, all in plain language. When it's done, the room hands off to your board.

Other commands:
- `magnolia update` — pull the latest engine (fast-forward only)
- `magnolia doctor` — check capabilities and get remediation if something's off

The board runs in the background, detached from the terminal: **closing the terminal window does
not stop the board.** Server output goes to `logs\task-server.log` in your Magnolia folder. If the
board can't start, `magnolia` prints the last lines of that log — read those first (a missing
Python package shows up there as `ModuleNotFoundError`).

---

## Optional extras
Onboarding will flag these if they're missing; you can add them anytime:
- **mgc** (Microsoft Graph CLI) — Outlook + Teams send, calendar invites. Binary from
  https://aka.ms/get/graphcli/latest/win-x64.zip (win-arm64.zip on ARM), extracted to a stable
  folder on your PATH. Onboarding handles the Microsoft sign-in; don't log in ahead of time.

---

## What to expect
- **Permission prompts** for winget/npm/downloads/clone during install — approve them or the
  installer stalls.
- **Connectors you already have** (Granola/M365/Jira/…) follow your claude.ai account; onboarding
  surfaces an "authorize on claude.ai" link for any that aren't connected yet. You should not need
  to re-authorize ones you already use.
- **`mgc login` may need admin consent** — the scope set includes `User.Read.All`, which some
  tenants require an admin to approve. If you're not an admin it may fail; that's fine, messaging
  and voice just stay disabled and onboarding continues.

## If something goes wrong
Run `magnolia doctor` — it detects and helps remediate a missing or degraded capability (Claude
not found, login expired, qmd not enabled, missing Python packages, etc.). The installer is
idempotent: re-running it is safe and will fast-forward an existing checkout rather than re-clone.

Common Windows fixes:
- **`python3` opens the Microsoft Store, or "Python was not found".** Open a *new* terminal (the
  shim PATH change only applies to new windows). If it persists, re-run the installer. You can also
  turn off the `python.exe` / `python3.exe` entries under *Settings → Apps → Advanced app settings →
  App execution aliases*.
- **Board won't start.** Read the log lines `magnolia` prints (or `logs\task-server.log`). For a
  missing package, run this from the Magnolia folder: `python3 -m pip install -r requirements.txt`.
- **Garbled characters or `UnicodeDecodeError`.** In a new PowerShell window, `echo $env:PYTHONUTF8`
  should print `1`; if not, re-run the installer.
