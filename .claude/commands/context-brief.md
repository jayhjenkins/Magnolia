# /project:context-brief

## MANDATORY: Use the workflow-context-brief Skill

**You MUST use the `workflow-context-brief` skill located at `.claude/skills/workflow-context-brief/SKILL.md`**

## Before Starting

1. **Announce**: "I'm using workflow-context-brief to produce the Context Brief"
2. **Read the skill**: Load `.claude/skills/workflow-context-brief/SKILL.md`
3. **Follow exactly**: Execute the skill as written

## Purpose

Run the PM discovery conversation and gather cited evidence (meetings, plus product analytics, support, and sales data when connected) into `{package}/context-brief.md`, the Phase 1 artifact every later phase reads.

## Arguments

- `--topic "feature X"` — Starting topic or problem statement
- `--from-transcript ./path/to/transcript` — Seed from a meeting transcript
- `--package <path>` — Use an existing package folder

## Output

`datasets/product/packages/{YYYY}/{slug}/context-brief.md` (template: `datasets/product/templates/context-brief.md`). An existing brief is never overwritten; a new version gets a `-v2` suffix.

## Gate

At least one clearly stated customer problem backed by data. Next step: `/project:press-release` or `/project:prep --skip-discover`.
