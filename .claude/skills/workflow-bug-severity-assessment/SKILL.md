---
name: workflow-bug-severity-assessment
description: Use when given a Jira bug link or key to classify — fetches the issue via Jira MCP and scores it against the Ticket Severity guidelines (Sev 1-4), with optional write-back to Jira's Severity field or a summary comment.
---

# Bug Severity Assessment

## Purpose

Turn a bare Jira bug link into a structured Severity 1-4 verdict against Vantaca's Ticket Severity guidelines in `reference.md` — the same rubric applied by hand, made repeatable. Severity classification only — Impact scoring is explicitly out of scope.

## When to Use

- User gives a Jira URL (`https://vantaca.atlassian.net/browse/VNT-XXXXX`) or bare key and asks for a severity read.

**When NOT to use:** the issue type isn't Bug/Security Defect (e.g. Feature, Unit, Epic) — the rubric is written for client-reported defects. Flag this and confirm with the user before applying it anyway.

## Workflow Steps

1. **Parse input** — extract the issue key via `[A-Z]+-\d+` from a URL or bare key.
2. **Fetch the issue**: `mcp__claude_ai_Jira__getJiraIssue` with `cloudId: "vantaca.atlassian.net"`, `issueIdOrKey`, `fields: ["summary","description","status","issuetype","priority","labels","components","assignee","reporter","created","updated","resolution","project","comment"]`, `responseContentFormat: "markdown"`.
3. **Error handling**:
   - Issue not found → surface Jira's error, ask the user to confirm the key.
   - Issue type isn't Bug/Security Defect → say so, ask before proceeding.
   - Jira MCP unavailable → tell the user, stop.
4. **Extract evidence** from description/comments: reproduction status, workaround (and whether it's self-service or staff-only), root cause (FE/BE, confirmed vs. suspected), clients/accounts affected, related/linked tickets, current `Priority` field (cross-check only — never authoritative).
5. **Determine Severity (1-4)** against `reference.md`'s Definition + Examples table. Cite the closest matching Example. State explicitly why it's not the tier above and not the tier below — the Sev 2/3 boundary is almost always "does a viable workaround exist."
6. **Output**, in chat:
   ```
   **Severity: N**
   - [rationale citing Definition + closest Example, why not one tier up/down]

   Cross-check: current Jira Priority is "<value>" [consistent / inconsistent — informational only]
   ```
7. **Optional write-back** — ask explicitly before either of these, every single time (external system, shared state):
   - Post the assessment as a comment via `mcp__claude_ai_Jira__addCommentToJiraIssue` — keep it to the verdict + rationale bullets, not a restated essay.
   - Set `customfield_10269` (Severity, select) to `{"value": "Severity N"}` via `mcp__claude_ai_Jira__editJiraIssue`.
   - Nothing else gets written. The Impact field (`customfield_10582`) is out of scope for this skill — do not compute or set it, even if asked in passing; if the user wants Impact scored, that's a separate explicit ask.
   - If the user declines, leave the ticket untouched — the chat output already answered the ask.

## Quality Gates

This repo's `quality-documentation-sync` gate targets `CURSOR-PM-SYSTEM.md`, `AGENTS.md`, `README.md`, and `.cursor/rules/*.mdc` — none of these exist in this repo. Only root `CLAUDE.md` and `.claude/CLAUDE.md` exist, and neither enumerates individual skills by name (skills auto-discover from `.claude/skills/`). No doc-sync edits are required beyond this skill and its command file.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Computing or writing an Impact score | Out of scope for this skill — severity only |
| Treating current Jira `Priority` as the answer | It's a cross-check signal, never authoritative — the rubric decides |
| Writing to Jira without asking that specific time | Confirm before every comment/field write, even if the user approved a write earlier in the session |
| Padding the Jira comment with restated narrative | Verdict + rationale bullets only — this repo's Jira edits stay minimal |
| Skipping the "why not one tier up/down" check | The Sev 2/3 line is almost always "does a viable workaround exist" — say so explicitly |

## Success Criteria

- Verdict is defensible against the specific Definition/Example language in `reference.md`, not vibes.
- No Impact score is computed or written.
- No Jira write (comment or Severity field) happens without that write being explicitly confirmed in the current turn.

## Related Skills

- **workflow-jira-home** — creates Jira issues; this skill only reads and optionally comments/edits an existing one.
