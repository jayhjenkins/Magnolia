---
name: workflow-pm-agent-activation-metric
description: Use when measuring PM agent activation — audits all in-flight and next-up Jira Feature cards for PM-OS/ship-it origin and general AI-enablement, outputs two dated markdown reports with per-card verdicts and summary stats
allowed-tools: Read, Grep, Glob, Bash, Agent, Write, mcp__claude_ai_Jira__searchJiraIssuesUsingJql, mcp__claude_ai_Jira__getJiraIssue, mcp__claude_ai_Jira__getAccessibleAtlassianResources
---

# PM Agent Activation Metric

Audit every in-flight and next-up Jira Feature card across the Vantaca org to measure two things:

1. **PM-OS fit** — was this card's spec produced by the `/ship-it` pipeline?
2. **AI-Enabled** — was AI meaningfully used in spec authoring via *any* process (PM-OS, AI-DLC, superpowers, team-templated AI drafting)?

Produce two dated markdown reports (in-flight + next-up) with a per-card table and summary stats.

## When to Use

- When Jay asks for updated PM agent activation numbers
- When preparing for leadership updates on AI adoption across product/engineering
- When comparing PM-OS penetration against the broader AI-DLC adoption

## When NOT to Use

- For a single card's assessment — just read the card directly
- For engineering velocity/throughput metrics — use `/estimate-velocity`
- For product analytics (Pendo, usage data) — use `context-pendo-analytics`

---

## Workflow

### Phase 0: Load prior run (skip re-assessment of unchanged cards)

Before pulling from Jira, check for the most recent prior output files in `datasets/product/agent-output/`:

```bash
ls -t datasets/product/agent-output/*_in-flight-feature-cards-pmos-ai-audit.md | head -1
ls -t datasets/product/agent-output/*_next-status-feature-cards-pmos-ai-audit.md | head -1
```

If prior files exist, parse each one to extract a lookup of `{card_key → {verdict_pmos, verdict_ai, basis, updated_date}}`. The `updated` timestamp from the prior run is the key — if a card appeared in the last run, you recorded its Jira `updated` field at that time.

**Carry-forward rule:** After pulling the fresh card list in Phase 1, compare each card's current `updated` timestamp against the prior run's recorded value. If:
- The card appeared in the prior run, AND
- Its `updated` timestamp has NOT changed since the prior run, AND
- Its prior verdict was **not** ⚠️ Unconfirmed (those always get re-assessed)

→ **Carry forward** the prior verdict and basis directly into the new output. Mark the basis with "(carried from {prior_date} run)" so it's clear this wasn't freshly assessed.

Only cards that are **new** (not in prior run), **updated** (timestamp changed), or **previously Unconfirmed** get dispatched to sub-agents for full assessment. This dramatically reduces Jira API calls and sub-agent work on repeat runs.

If no prior files exist, skip this phase entirely and assess everything fresh.

### Phase 1: Pull the card lists

Run two JQL queries against Jira. Use cloudId `d97732e2-8fd4-4a35-8c6c-eb2a8214cb23`.

**In-Flight bucket:**
```
issuetype = Feature AND statusCategory = "In Progress" ORDER BY project ASC, key ASC
```
This captures statuses `In Development` and `Now`.

**Next-Up bucket:**
```
issuetype = Feature AND status = "Next" ORDER BY project ASC, key ASC
```

For both, request fields: `summary, status, components, created, updated`

The result will likely be a large JSON. Use `jq` via Bash to extract a flat list: `key, project.key, status.name, created, updated, components[].name, summary` per card.

After extraction, apply the Phase 0 carry-forward rule. Split the remaining cards (new, updated, or previously Unconfirmed) into sub-agent batches. Cards that moved between buckets (e.g., was "Next" last run, now "In Development") should be re-assessed since their status changed.

### Phase 2: Dispatch sub-agents for assessment

For **each** bucket (in-flight and next-up), split the card list into up to 3 batches by team/component:

| Batch | Cards matching | Why separated |
|---|---|---|
| Home | component includes `Vantaca HXP` | Needs local ship-it package cross-check |
| BaaS / Integrations | project = `INT` | Has its own Pre_AI-DLC convention and BaaS template |
| Everything else | Remaining VNT cards | Heterogeneous teams with distinct template variants |

Launch sub-agents in parallel (up to 3 per bucket, so up to 6 total across both buckets). Each sub-agent receives:

1. The full assessment rules (copied below — do NOT reference memory files; the sub-agent has no conversation context)
2. The specific card keys to fetch (only the ones that need fresh assessment — not carried-forward cards)
3. The Jira cloudId
4. Which fields to pull per card: `summary, description, customfield_10783, created, updated, components, status`
5. Return format: a markdown table with columns `Card | App | Status | Created | Updated | PM-OS Fit | AI-Enabled? | Basis`
6. For the Home batch ONLY: instructions to cross-check against `datasets/product/packages/{current_year}/*/PRD_*.md` on disk

### Phase 3: Compile and write output

Merge the sub-agent tables into two markdown files. Write to `datasets/product/agent-output/` using date-first naming:

- `{YYYY-MM-DD}_in-flight-feature-cards-pmos-ai-audit.md`
- `{YYYY-MM-DD}_next-status-feature-cards-pmos-ai-audit.md`

Each file structure:

```
# {Title}

**Date:** {YYYY-MM-DD}
**Scope:** {description of what was pulled, card count}

## Method
{Brief description of the two assessments}

## Full table
{Merged table from sub-agents + carried-forward rows, sorted: PM-OS Yes first, then by project/key. Include Updated column for carry-forward tracking.}

## Stats
{Summary counts and percentages}
```

Markers: ✅ Yes / ❌ No / ⚠️ Unconfirmed / ➖ N/A

Table columns: `Card | App | Status | Created | Updated | PM-OS Fit | AI-Enabled? | Basis`

The `Updated` column records the Jira `updated` timestamp at time of assessment — this is what Phase 0 of the next run uses to decide whether to carry forward or re-assess.

### Phase 4: Print summary to user

After writing both files, print a concise summary:

- In-Flight: X cards total, Y PM-OS (Z%), A AI-Enabled (B%)
- Next-Up: X cards total, Y PM-OS (Z%), A AI-Enabled (B%)
- Cards carried forward from prior run: N (unchanged since {prior_date})
- Cards freshly assessed this run: M (new, updated, or previously Unconfirmed)
- Any Unconfirmed rows that need Jay's direct call (list them)

---

## Assessment Rules — PM-OS Fit

These are the rules sub-agents must apply. Copy them verbatim into each sub-agent prompt.

### Decision tree (apply in order, stop at first match):

**Step 1 — Component check.** Read the card's `components` field. If it includes `Vantaca HXP` → this is a Home card. Otherwise → non-Home.

**Step 2 — Date gate (non-Home only).** PM-OS rolled out org-wide on **2026-07-10**. Any non-Home card with `created` before that date → **❌ No** automatically. No content review needed.

**Step 3 — Spec Reference field (customfield_10783).** If the field contains a URL matching `PM-OS/product/packages/` or `Documents/PM-OS/product/packages/` → **✅ Yes**. This is the strongest possible positive signal (the card explicitly links to a ship-it PRD via SharePoint).

**Step 4 — Local package cross-check (Home batch sub-agent only).** Search `datasets/product/packages/{current_year}/*/PRD_*.md` on disk. For each PRD found, read its `# {Title}` line and `## Changelog` section. Match by topic similarity to the card summary.
- If a match is found and the PRD changelog's earliest-draft date is within ~2 weeks of the card's `created` date → **✅ Yes**.
- Topic match but large date gap (>1 month) → investigate further. The card might be a later addition that went through a different process (e.g., VNT-44486 was a later addition to the dynamic-forms PRD package but its spec actually went through AI-DLC, not ship-it). Check the card's description for which process it cites.

**Step 5 — Description fingerprint (Home cards, steps 3-4 inconclusive).** Look for ship-it template elements:
- Explicit `Full PRD:` link pointing to SharePoint/OneDrive
- Sections: `## Value to the Management Company`, `## Build Sequence` (P0/P1/P2), `## Metrics and Learning Agenda`, `## Open Questions / Tracked Assumptions`, `## PRD Status Reference`
- Status emoji: 🚧 Drafting / 🏃 Actionable / 🔒 Closed / ❗ Abandoned
- If none present but the card is substantive → **⚠️ Unconfirmed** (NOT "No" — descriptions can be stale)

**Step 6 — Non-product filter.** If the card is pure infrastructure/QA/tech-debt/test-coverage/bug-bucket/internal-tooling (not a product-spec-driven feature) → **➖ N/A**. Examples: integration test backfill, agentic bug automation, Datadog logging migration, legacy helper scripts.

**Step 7 — Confident "No" (Home cards only).** Only call **❌ No** if:
- Description is genuinely thin/empty AND no local package match AND Spec Reference is empty or points to a non-PM-OS source (GHE inception, GitBook, ADO PR), OR
- Description explicitly cites a different process as its source (e.g., "Inception document: home/VNT-XXXXX in the specs repo")

---

## Assessment Rules — AI-Enabled

### Decision tree (apply in order, stop at first match):

**Step 1 — PM-OS fit already Yes → ✅ Yes.** PM-OS is itself an AI pipeline; definitional.

**Step 2 — Explicit positive citations** (in description text OR Spec Reference field):
- URL/path containing `vantaca/specs` with `inception.md` or `inception/` → **✅ Yes** (GHE AI-DLC)
- URL containing `vantaca-ai-hub` or `vantaca.gitbook.io/vantaca-ai-hub` → **✅ Yes** (GitBook AI-DLC)
- Path containing `docs/superpowers/inceptions/` → **✅ Yes** (Claude Code superpowers plugin)
- Any inception doc citation in GHE → **✅ Yes**
- Description references a **markdown file** (`.md` path) containing deeper requirements or a spec → **✅ Yes**. A markdown-file-based spec workflow is itself a tell-tale sign of an AI-enabled process at Vantaca — humans writing specs by hand use Word, Guru, or Confluence, not `.md` files in a git repo.

**Step 3 — Explicit negative markers.**
- Spec Reference contains `Pre_AI-DLC` or `PreAIDLC.com` → **❌ No** (confirmed placeholder meaning "predates the AI-DLC process" at Vantaca)

**Step 4 — Team-template richness test** (no citation needed; the template itself is evidence):

*BaaS/Integrations:* Summary → High-Level Acceptance Criteria (quantified, 5+ bullets) → Scenarios table (multi-row, inputs/expected results) → Out of Scope → present with real content → **✅ Yes**

*Mobile-Manage:* Goals (numbered, quantified with "75%+", "60%+"…) → Non-Goals (each with explicit `*Rationale:*` clause) → **✅ Yes**

*Revenue Manager / Vantaca Pay:* Summary → Why This Matters → Scope → Customer Outcome → Out of Scope → References, OR Overview → Problem → Goal → Technical Approach → Units of Work → Open Questions → Timeline → **✅ Yes**

*Vantaca Core / other:* Business case → quantified success metrics → scored/rated initiatives → **✅ Yes**

**Calibration:** the template must *actually be there* with real content — not just a few bullets. The test: would this description take a human more than 15 minutes to write from scratch? If yes, lean AI-enabled. If it reads like 2 minutes of typing, don't over-credit.

**Step 5 — Child ticket evidence.** If the Feature card itself is thin or ambiguous, check its child/sub-tickets (Units, Stories) for AI-enablement signals. Use `mcp__claude_ai_Jira__searchJiraIssuesUsingJql` with `parent = {key}` to pull child tickets, then spot-check 2-3 for structured content, markdown spec references, or AI-DLC citations. If the children show clear AI-enabled authoring → **✅ Yes** for the parent Feature, even if the parent's own description is thin. Rationale: a Feature card can be a lightweight container while the real AI-assisted spec work lives in the child tickets.

**Step 6 — Thin/empty → ❌ No.** One-paragraph descriptions, raw bullet fragments, pasted data tables, "items I believe we need" lists, empty descriptions, bug-bucket placeholders — AND child tickets (if checked) also show no AI signals. Validated as correct: INT-11224, VNT-35168, VNT-42879, VNT-36472, VNT-44177.

**Step 7 — Genuinely ambiguous → ⚠️ Unconfirmed.** Some structure but noticeably thinner than the team's normal template, no citation either way, and child tickets inconclusive or not checked. Note what's missing; let Jay resolve.

---

## Common Mistakes

| Mistake | Why it's wrong | Correct approach |
|---|---|---|
| Calling a Home card "No" for PM-OS because description is thin | Jira descriptions can be stale and predate real ship-it work | Use ⚠️ Unconfirmed; let Jay confirm |
| Requiring an explicit citation for AI-Enabled | Team-template richness IS evidence; over-conservative calls were corrected 5 times in the original pass | Apply the template-richness test |
| Forcing PM-OS/AI verdict on infra/QA tickets | Test backfill, logging, bug buckets aren't product-spec-driven | Use ➖ N/A |
| Guessing at auth-walled doc contents | SharePoint, GHE Enterprise, GitBook links return 401/empty shells | Classify the link type; don't guess at contents |
| Ignoring markdown file references in descriptions | A `.md` file path in a git repo is a tell-tale sign of an AI-enabled spec process; humans use Word/Guru/Confluence | Check for `.md` references — they're a positive signal |
| Judging only the Feature card, not its children | A Feature can be a thin container while the real AI-assisted work lives in child tickets | Spot-check 2-3 child tickets when the parent is ambiguous |
| Including non-Feature issue types | Units, Stories, Bugs, Tasks are downstream work items | Feature type only |

## Success Criteria

- Both output files written with the correct date-first naming to `datasets/product/agent-output/`
- Every Feature card in both status buckets is represented in exactly one row
- Each row has a concrete, evidence-based Basis (not "seems like it" or "probably")
- Stats section percentages are mathematically correct
- Unconfirmed rows are explicitly called out for Jay's resolution

## Related Skills

- `workflow-prd-creation` — the ship-it pipeline that produces the PRDs being detected
- `workflow-jira-home` — creates Feature cards on the Home board
- `quality-pendo-tag-audit` — unrelated but uses a similar sub-agent-per-metric pattern
