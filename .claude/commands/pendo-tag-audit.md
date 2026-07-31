---
description: Audit whether a Pendo page/feature's tagging (Include/Exclude rules, CSS selectors) actually isolates the intended element before trusting its data
---
MANDATORY: Use the `quality-pendo-tag-audit` skill at `.claude/skills/quality-pendo-tag-audit/SKILL.md`.

Audit the Pendo tagging for the page/feature/area named in the argument below — surface hidden duplicates, compare Include/Exclude rules against the live DOM, and report which ID is canonical, which (if any) is mistagged, and what the corrected rule should be. Use this any time a metric looks wrong, not just at launch time.

$ARGUMENTS
