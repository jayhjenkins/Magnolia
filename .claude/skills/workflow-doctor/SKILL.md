---
name: workflow-doctor
description: Use when a capability is missing/degraded/needs re-auth, when the user asks to "fix"/"set up"/"authorize" a tool or integration, or during onboarding's Doctor pass — detects with scripts/doctor.py and remediates conversationally.
allowed-tools: Bash, Read, Edit
---

# Doctor — detect, then remediate

You are the remediation half of the Doctor. Detection is deterministic Python
(`scripts/doctor.py`); your job is the adaptive, conversational fixing. The human
does only the irreducible minimum — clicking "Authorize."

## Loop

1. **Detect.** Run `python3 scripts/doctor.py detect`. Read `profile/capabilities.json`.
2. **Triage** each capability whose `status` is not `ok`/`running`:
   - **local** (qmd, pandoc, claude_cli, msgraph_cli): run the install via Bash — use the
     exact command in the capability's `remedy` field (it already encodes the right per-OS
     install). Capabilities with `recommended: true` (qmd, pandoc, msgraph_cli) are **STRONGLY
     recommended, not throwaway-optional**: they won't *block* onboarding, but say plainly what
     the user loses without each (the `rationale`) and offer to install it now. Posture: "you
     don't have to, but you really should — here's what it unlocks." Don't shrug them off as
     optional; don't hard-block either.
     - **qmd specifically**: the ONE correct tool is **`npm install -g @tobilu/qmd`**
       (https://github.com/tobi/qmd, needs Node ≥ 22). Do NOT `brew install qmd` or install any
       other repo named "qmd" — they are different tools and will break the qmd MCP. After
       install, qmd runs as the MCP via `qmd mcp` (already wired in the repo's `.mcp.json` as the
       bare `qmd` command); on first launch the user approves it via `/mcp`.
   - **feed/transcript** `needs_reauth`: depends on the active provider (`probe_transcript`
     keys off `transcript.provider`).
     - **Otter** `needs_setup` (deps missing — `cap["missing"]` lists them): the Otter feed needs the
       optional transcript extras, which the core install does NOT ship. Have the user install them
       FIRST — `pip install -r requirements-transcript.txt && python3 -m playwright install chromium` —
       otherwise `otter_auth.py` crashes on import. (Or note Granola needs none of this.) Only after
       the deps are present does the probe move on to the session check.
     - **Otter** `needs_reauth`: deps are present but the saved session expired. Walk the user through
       `python3 scripts/otter_auth.py` (a browser opens for Microsoft sign-in). Inherently manual —
       explain warmly, wait for them.
     - **Granola**: the MCP isn't connected yet. The probe stays `needs_reauth` until a successful
       sync writes its marker (`granola_downloaded.json`). Walk the user through: connect Granola via
       `/mcp`, then finish the one-time signup at granola.ai/mcp-signup (the MCP is a claude.ai
       connector you can't refresh from the shell). Note transcripts need a paid Granola plan. Then
       run `python3 scripts/granola_sync.py` once to seed the marker and confirm it flips to `ok`.
   - **remote** (jira/m365/pendo/…): you cannot refresh these from the shell — they are
     claude.ai connectors. Tell the user plainly: open claude.ai → Connectors → authorize X.
     Then verify by making one cheap read-only call to that MCP. If it works, set that
     capability's `status` to `ok` and `last_seen` to today in `capabilities.json`; if it
     fails, set `needs_reauth` with a short `reason`.
   - **service** (`server`, the task server): if `status` is `down`, the remedy is to start
     the task server (`scripts/run_task_server.sh` on macOS, or `server_lib.start` during
     onboarding) — not a brew install or re-auth.
3. **Re-detect** (`doctor.py detect`) and confirm what's now green.
4. **Report** calmly: what's working, what's still degraded (and that its features are simply
   disabled until fixed — nothing is broken), and the single next action if any.

## Rules
- Never claim something is fixed without re-running detection and seeing it.
- Graceful degradation: a still-missing capability disables only its own features. Never block.
- Plain language. No git, no model IDs, no jargon. Blast-radius in words a COO reads easily.
