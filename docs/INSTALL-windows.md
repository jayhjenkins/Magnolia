# Installing Magnolia — Windows

Get the prerequisites on your Windows machine, land the repo in the right place, then hand off to
the conversational `onboard me` flow. Paste the prompts straight into Claude Code.

> **Magnolia runs natively on Windows.** You do **not** need WSL, Ubuntu, or any Linux setup —
> just the native Windows tools below (winget + PowerShell + Python). If Claude ever suggests
> installing WSL/Ubuntu to get Magnolia working, that's a mistake: decline it and report the
> underlying error instead.

## The two-prompt shape (and why)
Installation is **two prompts with a restart in between**, on purpose:
1. **Prompt 1** installs prerequisites + clones the repo.
2. **You fully quit and reopen Claude Code.** Newly installed tools land on your `PATH`, and a
   *running* Claude Code session can't see them until it restarts and re-reads your shell. (This
   is the "hot-swap" gotcha — don't skip the restart.)
3. **Prompt 2** is just `onboard me`.

---

## ⚠️ Where to put Magnolia (read this first)
**If you already use Claude Code, clone Magnolia *inside the same workspace where Claude Code
already works for you*** — the folder where your corporate integrations (Granola, Microsoft 365,
Jira, Pendo, Databricks) and your personal skills already show up.

Why it matters: those integrations are **claude.ai account connectors**. They should follow you
everywhere, but a brand-new, never-opened folder can come up *untrusted* with connectors not yet
enabled — which makes Magnolia look like it can't see integrations you actually have. Landing
Magnolia next to your existing Claude Code work avoids that, and lets it inherit your skills too.

You do **not** need to re-architect your folders. Just drop the `Magnolia` folder in the place
your Claude Code already lives. If you don't use Claude Code yet, `%USERPROFILE%\dev` is a fine home.

---

## Strongly recommended tools (install these up front)
These aren't busywork — each unlocks real capability, and skipping them degrades quality. Install
them now; onboarding won't *block* without them, but you really should have them.

| Tool | Unlocks | Install |
|---|---|---|
| **qmd** | Semantic search across all your meetings/notes/docs (the killer feature) | `npm install -g @tobilu/qmd` — needs **Node ≥ 22**. The correct repo is **https://github.com/tobi/qmd** (do NOT install any other "qmd"). |
| **mgc** (Microsoft Graph CLI) | Outlook + Teams send, calendar invites | binary from https://aka.ms/get/graphcli/latest/win-x64.zip (win-arm64.zip on ARM), on PATH |
| **pandoc** | Word-doc creation / publish-package | `winget install --id JohnMacFarlane.Pandoc -e` |

---

## Prompt 1 — paste into a fresh Claude Code session

```
You're installing a tool called Magnolia on my Windows machine and getting it ready for first-run
setup. Do the steps in order, explain each in plain language, and ASK before anything that needs
my approval. I'll see permission prompts for installs/downloads — that's expected. Do NOT start
"onboard me" — stop at the end and tell me to restart you.

1. Confirm this is Windows and tell me the architecture (x64 or ARM64).
2. Ask me: "Do you already use Claude Code? If so, where — what folder do you usually run it in?"
   - If yes: we'll clone Magnolia INSIDE that same workspace so it inherits my existing
     integrations and skills. Confirm the target path with me before cloning.
   - If no: use %USERPROFILE%\dev\Magnolia (create %USERPROFILE%\dev if needed).
3. Make sure these are installed; install any missing via winget:
   - Git for Windows  → winget install --id Git.Git -e            (REQUIRED — provides the Git Bash shell Magnolia and Claude Code rely on)
   - Node.js (>= 22)  → winget install --id OpenJS.NodeJS -e       (needed for qmd)
   - Python 3         → winget install --id Python.Python.3.12 -e
   - Pandoc           → winget install --id JohnMacFarlane.Pandoc -e
4. Install qmd (semantic search): npm install -g @tobilu/qmd
   The correct qmd is https://github.com/tobi/qmd — do NOT install any other tool named "qmd".
5. Install the Microsoft Graph CLI (mgc): download https://aka.ms/get/graphcli/latest/win-x64.zip
   (win-arm64.zip on ARM), extract to a stable folder (e.g. C:\Users\<me>\tools\mgc), add that
   folder to my USER PATH, and verify `mgc --version` in a NEW PowerShell window. Do NOT log me in
   yet — the setup flow handles the Microsoft sign-in.
6. Install Python deps: python -m pip install ruamel.yaml pytest
7. Clone the repo to the location we agreed in step 2:
   git clone https://github.com/jayhjenkins/Magnolia.git "<agreed path>"
8. STOP. Tell me exactly: "Setup's done. Fully quit Claude Code and reopen it (so it picks up the
   newly installed tools on PATH), then cd into your Magnolia folder and type: onboard me."
```

---

## Then: restart + Prompt 2
**Fully quit and reopen Claude Code.** Then, from the Magnolia folder:
```
cd <your Magnolia folder>
```
```
onboard me
```

On the first run, if Magnolia asks about a connector (Granola/M365/Jira/…) you already have,
open `/mcp` to confirm it's enabled for this folder and trust the folder — you should **not** need
to re-authorize anything; these are account-level connectors.

---

## ⚠️ Critical Windows gotcha: Python must be reachable from Git Bash

Magnolia's background agents (the "Start Agent" button on the board) run Python scripts through
Git Bash — **not** PowerShell. Windows installs Python on the PowerShell/CMD PATH, but Git Bash
uses its own view of the PATH and **will not see Python until you restart after installation**.

**The symptom:** agents silently exit with "exit code 1" the moment they start, nothing gets
drafted, and the task card shows a failure with no helpful message.

**The fix:** after installing Python (Prompt 1, step 3), the restart in step 8 is what makes
Python visible to Git Bash. Don't skip it. If agents still fail after restarting, run this
inside Claude Code to verify:

```
! python --version
```

If that returns `Python 3.x.x`, Git Bash can see Python and agents will work.
If it returns "command not found", Python is installed but not yet on Git Bash's PATH.

**Common causes and remedies:**

| Cause | Fix |
|---|---|
| Installed Python but haven't restarted Claude Code | Fully quit and reopen Claude Code |
| Python installed as a "Store app alias" (Microsoft App Store stub) | Re-install via winget: `winget install --id Python.Python.3.12 -e --override "/quiet InstallAllUsers=1 PrependPath=1"` |
| Python not added to system PATH during install | Rerun Python installer → "Modify" → check "Add Python to PATH" |
| Git Bash reads a stale PATH cache | Restart your machine once after the full install sequence |

The test is always the same: `! python --version` inside Claude Code. Green → agents work.
Red → one of the remedies above will fix it.

---

## What to expect
- **Permission prompts** for winget/npm/downloads/clone — approve them or Prompt 1 stalls.
- **`mgc login` (during onboarding) may need admin consent** — the scope set includes
  `User.Read.All`, which some tenants require an admin to approve. If you're not an admin it may
  fail; that's fine — messaging/voice just stay disabled and onboarding continues.
