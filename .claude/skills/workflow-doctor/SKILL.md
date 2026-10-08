---
name: workflow-doctor
description: Use when a capability is missing/degraded/needs re-auth, when the user asks to "fix"/"set up"/"authorize" a tool or integration, or during onboarding's Doctor pass — detects with scripts/doctor.py and remediates conversationally.
allowed-tools: Bash, Read, Edit
---

# Doctor — detect, then remediate

You are the remediation half of the Doctor. Detection is deterministic Python
(`scripts/doctor.py`); your job is the adaptive, conversational fixing. The human
does only the irreducible minimum — clicking "Authorize."

Magnolia runs natively on macOS and Windows. Give OS-neutral commands (`python3 ...` works on
both — Windows has a `python3` shim). If a fix is genuinely OS-specific, say which OS it's for.
Never assume a port, path, or username — read them from `profile/` (via `scripts/profile_lib.py`)
or from the capability's own fields.

## Loop

1. **Detect.** Run `python3 scripts/doctor.py detect`. Read `profile/capabilities.json`.
2. **Triage** each capability whose `status` is not `ok`/`running`:
   - **local** (qmd, pandoc, claude_cli, msgraph_cli): run the install via Bash — use the
     exact command in the capability's `remedy` field (it already encodes the right per-OS
     install; when it lists one command per OS, run only the one for this machine). Capabilities
     with `recommended: true` (qmd, pandoc, msgraph_cli) are **STRONGLY recommended, not
     throwaway-optional**: they won't *block* onboarding, but say plainly what the user loses
     without each (the `rationale`) and offer to install it now. Posture: "you don't have to, but
     you really should — here's what it unlocks." Don't shrug them off; don't hard-block either.
     - **qmd specifically**: the ONE correct tool is **`npm install -g @tobilu/qmd`**
       (https://github.com/tobi/qmd, needs Node ≥ 22). Do NOT install any other package or repo
       named "qmd" — they are different tools and will break the qmd MCP. After install, qmd runs
       as the MCP via `qmd mcp` (already wired in the repo's `.mcp.json` as the bare `qmd`
       command); on first launch the user approves it via `/mcp`.
   - **feed/transcript**: depends on the active provider (`transcript.provider` in
     `profile/integrations.yaml`; `probe_transcript` keys off it). Granola and Otter are equal
     options — fix whichever one is active. Either way, the board runs the sync hourly by itself on
     both OSes; there is no scheduler or background agent for the user to install.
     - **Granola** `needs_reauth`: the connector isn't working yet. The probe stays there until a
       successful sync writes its marker (`granola_downloaded.json`). Granola transcripts come
       through the claude.ai Granola connector and require a **paid Granola plan** — say that
       first. Walk the user through: connect Granola via `/mcp` in Claude Code (or claude.ai →
       Settings → Connectors) and, if Granola prompts for it, finish its one-time MCP signup. Then
       run `python3 scripts/granola_sync.py` once and confirm the probe flips to `ok`.
     - **Otter** `needs_setup` (deps missing — `cap["missing"]` lists them): the Otter feed needs
       the optional transcript extras, which the core install does NOT ship. Install them into
       Magnolia's own transcript environment, then sign in:
       `python3 -c "import sys, subprocess; sys.path.insert(0,'scripts'); import ensure_venv; py = ensure_venv.ensure(); subprocess.run([py, '-m', 'playwright', 'install', 'chromium'], check=True)"`
       (Or note that Granola needs none of this.)
     - **Otter** `needs_reauth`: deps are present but the saved session expired. Re-run the sign-in
       (a browser opens for Microsoft sign-in) — inherently manual; explain warmly, wait for them:
       `python3 -c "import sys, subprocess; sys.path.insert(0,'scripts'); import ensure_venv; subprocess.run([ensure_venv.ensure(), 'scripts/otter_auth.py'], check=True)"`
     - **stale / degraded** (either provider): read the sync log the capability's `detail`/`remedy`
       names under `logs/` for the root cause before suggesting anything.
   - **remote** (jira/m365/pendo/…): you cannot refresh these from the shell — they are
     claude.ai connectors. Tell the user plainly: open claude.ai → Connectors (or `/mcp` in Claude
     Code) → authorize X. Then verify by making one cheap read-only call to that MCP. If it works,
     set that capability's `status` to `ok` and `last_seen` to today in `capabilities.json`; if it
     fails, set `needs_reauth` with a short `reason`.
   - **service** (`server`, the board): if `status` is `down`, the fix is to have the user run
     `magnolia` — the launcher starts the board on its configured port and opens it. Never pick a
     port or start a second server yourself; never suggest a brew install or re-auth for this.
3. **Re-detect** (`doctor.py detect`) and confirm what's now green.
4. **Report** calmly: what's working, what's still degraded (and that its features are simply
   disabled until fixed — nothing is broken), and the single next action if any.

## Rules
- Never claim something is fixed without re-running detection and seeing it.
- Graceful degradation: a still-missing capability disables only its own features. Never block.
- Plain language. No git, no model IDs, no jargon. Blast-radius in words a COO reads easily.
- OS-neutral by default; label anything that only applies to macOS or only to Windows.
