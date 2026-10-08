---
name: workflow-context-brief
description: Use when starting a product package (Phase 1 of /project:ship-it or /project:prep, or /context-brief) - runs the PM discovery conversation, gathers cited evidence, and writes context-brief.md
---

# Context Brief

## Purpose

Phase 1 of the product package. Turn a topic, transcript, or hunch into `{package}/context-brief.md`: a cited, evidence-backed statement of the customer problem that every downstream phase reads. **Core principle: no problem without evidence.** Every fact carries a citation; anything uncited is labeled as a PM assertion or assumption.

## When to Use

- Phase 1 of `/project:ship-it` or `/project:prep`
- `/context-brief` invoked directly
- A PM says "I want to build X" and no `context-brief.md` exists for it yet

**When NOT to use:** a brief already exists and the PM only wants vision work (go to `workflow-vision-clarifier`); the work is a bug fix.

## Inputs

- Package folder `datasets/product/packages/{YYYY}/{slug}/` (created by the calling command; create it if invoked standalone)
- Optional `--topic "..."` and/or `--from-transcript <path>`
- The PM, for the conversation
- Operator identity from the profile, never a literal: `python3 -c "import sys; sys.path.insert(0, 'scripts'); import profile_lib as p; print(p.display_name(), '|', p.company())"`

## Output

`{package}/context-brief.md`, following `datasets/product/templates/context-brief.md`. If a brief already exists, write `context-brief-v2.md` (next free suffix) and leave the original untouched.

## Workflow

### Step 1: Seed

Read the topic and/or transcript. Draft a one-sentence working problem statement and a short list of search terms (the customer's words, not ours). If a transcript was given, pull its quotes and named customers as the first evidence rows.

### Step 2: Gather evidence (all sources optional)

Run what is available; record each finding as an `E#` row with source, date, and what it shows. If a source is unavailable (tool missing, not connected, auth error, empty result), note it under **Sources not available** and continue. Never stall on a missing source.

| Source | How | Feeds |
|--------|-----|-------|
| Meetings, research, prior artifacts | `context-search` skill (qmd), concept queries in customer language; grep for exact customer names | Customer voice, named customers, personas, workaround |
| Product usage | `context-pendo-analytics` skill | Behavioral baselines, reach |
| Support tickets / sales calls / eng work items | `context-databricks-analytics` skill | Evidence counts, themes, reach |
| Research library | `context-research-gathering` skill | Market & competitive context |

Check for an existing package or PRD on the same topic (`context-search` over product artifacts). If one exists, tell the PM and link it.

### Step 3: Discovery conversation

Present a short evidence summary, then ask only what the evidence did not answer. One question at a time; follow up on vague answers.

1. Who exactly has this problem? (named segment, 3–5 personas)
2. What do they do today, and what does it cost them?
3. Why now? What changes if we wait a quarter?
4. What jobs are they trying to get done?
5. What constraints are fixed (data, platform, commercial, compliance, dates)?
6. What signal would tell us this worked, and from what baseline?
7. What do we not know yet?

Record PM answers that have no evidence as *(PM assertion)*.

### Step 4: Write the brief

Fill every template section. Use `[E#]` citations inline. Keep it under ~1,500 words; downstream agents read it in full.

### Step 5: Gate 1 self-check

PASS requires **at least one clearly stated customer problem backed by data**: the Problem Statement cites at least one `E#` that is a real source (meeting, usage, ticket, call, research), not a PM assertion. Write the result in the header's Gate 1 field. On FAIL, tell the PM what evidence is missing and ask for it before the caller proceeds.

## Downstream Consumers

| Consumer | Reads |
|----------|-------|
| `workflow-vision-clarifier` | Problem, who has it, workaround, customer voice |
| `workflow-devils-advocate` | Problem, evidence, personas |
| `workflow-agentic-api-designer` | Problem, use cases / jobs |
| `workflow-prd-creation` | Problem, evidence, behavioral baselines, open questions |
| `workflow-ambition-expander` | Evidence, competitive context |
| `workflow-red-team-reviewer` | Personas (3–5) |
| `workflow-swag-modeler` | Evidence, reach, market context |

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Writing a solution as the problem | Restate as the customer's barrier; solutions go to Phase 2 |
| Uncited claims stated as fact | Add `[E#]` or label *(PM assertion)* / *(assumption)* |
| Blocking because analytics is not connected | Note it under Sources not available; continue |
| Passing Gate 1 on PM assertion alone | Gate 1 needs at least one real data source |
| Overwriting an existing brief | Write `context-brief-v2.md` |
| Hardcoding a person, team, or company name | Read it from the profile |

## Success Criteria

- `{package}/context-brief.md` exists with all template sections filled or explicitly marked n/a
- Every evidence row has a resolvable source
- Gate 1 field reads PASS, or the PM was told exactly what is missing

## Related Skills

- **context-search**, **context-pendo-analytics**, **context-databricks-analytics**, **context-research-gathering** — evidence sources
- **context-meeting-synthesis** — for deeper multi-transcript signal extraction
- **workflow-vision-clarifier** — the next phase
