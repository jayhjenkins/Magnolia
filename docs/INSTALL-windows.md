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
- install prerequisites via winget (git, node, python, pandoc) and **qmd** (semantic search)
- confirm Claude Code is present (or stop and tell you to install it)
- sign you into Claude **only if you aren't already** (a browser opens — this is the one
  interactive moment for a brand-new user)
- clone Magnolia to `%USERPROFILE%\Magnolia` (or your `MAGNOLIA_DIR`)
- seed folder trust + qmd enablement
- add the repo's `bin` folder to your user PATH

> **PATH hot-swap gotcha:** winget does not refresh the *current* shell's PATH. On a bare machine,
> the `npm install -g @tobilu/qmd` (or `git`) step right after a fresh Node/Git install may not be
> found in the same session. If the installer errors there, **open a new PowerShell window and run
> the one-liner again** — it's idempotent and will pick up where it left off.

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
not found, login expired, qmd not enabled, etc.). The installer is idempotent: re-running it is
safe and will fast-forward an existing checkout rather than re-clone.
