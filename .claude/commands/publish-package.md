# /project:publish-package

## MANDATORY: Use the publish-package Skill

**You MUST use the `publish-package` skill located at `.claude/skills/workflow-publish-package/SKILL.md`**

## Before Starting

1. **Announce**: "I'm using publish-package to look up the Word links for a product package"
2. **Read the skill**: Load `.claude/skills/workflow-publish-package/SKILL.md`
3. **Follow exactly**: Execute the skill as written

## Purpose

List the Word Online URLs for the documents in a product package that already have a Word copy (read-only: `python3 scripts/doc_sync.py urls <package> --json`). This command never creates or syncs Word docs. Publishing to Word happens only from the board's markdown editor 3-dot menu ("Publish to Word" / "Sync with Word").

## Arguments

- `$ARGUMENTS` — Package folder path or slug (e.g., `dynamic-forms` or `datasets/product/packages/2026/dynamic-forms/`)
