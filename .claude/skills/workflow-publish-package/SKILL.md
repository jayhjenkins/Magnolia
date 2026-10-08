---
name: workflow-publish-package
description: Use when the PM wants the Word Online links for a product package's documents - lists the read-only Word URLs for package files that already have a Word copy (never creates or syncs Word docs; publishing happens only from the board editor's 3-dot menu)
triggers:
  - publish package
  - share package documents
  - Word Online links for package
  - Word links for package
  - package Word URLs
type: workflow
---

# Package Word Links Skill

## Purpose

List the Word Online URLs for the markdown files in a product package that **already have a Word copy**, so the PM can share them or paste them into Jira.

**This skill never pushes a Word doc.** It runs a read-only lookup only. The single place the system creates or updates a Word copy is the board's markdown editor 3-dot menu:
- **Publish to Word** - first Word copy for a document.
- **Sync with Word** - overwrite the existing Word copy from markdown.

Never run `doc_sync.py sync-one`, `sync-folder`, `sync-all`, or `sync-back` from this skill.

## When to Use

- User invokes `/project:publish-package`
- User asks to "share" a product package or wants its Word links
- After `/project:ship-it` or `/project:build`, when the PM needs URLs to hand to stakeholders or Jira

**When NOT to use:**
- The PM wants a document put into Word for the first time, or wants Word refreshed from markdown - point them to the editor 3-dot menu ("Publish to Word" / "Sync with Word").

## Workflow

### Step 1: Resolve Package Folder

**If a full path is provided** (e.g., `datasets/product/packages/2026/dynamic-forms/`):
- Verify the directory exists
- Verify it contains `.md` files

**If only a slug is provided** (e.g., `dynamic-forms`):
- Search `datasets/product/packages/*/` for a matching folder
- If multiple matches across years, ask user to disambiguate
- If no match, list available packages

**If no argument provided:**
- List available packages under `datasets/product/packages/`
- Ask user to select one

### Step 2: Look up existing Word links (read-only)

```bash
python3 scripts/doc_sync.py urls datasets/product/packages/{YYYY}/{slug}/ --json
```

Output shape: `{"folder": "...", "files": [{"file": "...", "url": "..."}]}`. Only `.md` files whose Word copy already exists appear in `files`. Nothing is written.

### Step 3: Present Results

List every `.md` file in the package, split by whether it has a Word copy:

```
## Word links: {slug}

| Document | Word Online |
|----------|-------------|
| PRD_dynamic-forms.md | [Open in Word](url) |
| press-release-internal.md | [Open in Word](url) |

No Word copy yet:
  - context-brief.md
  - press-release-external.md
```

If any file has no Word copy, add: "To get a Word copy, open the file in the board editor and use **Publish to Word** from the 3-dot menu, then re-run this command."

## Error Handling

| Error | Action |
|-------|--------|
| Package folder not found | List available packages, ask user to pick |
| No .md files in folder | Warn user, suggest checking the path |
| `files` is empty | Say no package documents have a Word copy yet; point to the editor 3-dot menu |
| doc_sync not configured (sync_config.yaml missing) | Say Word links are unavailable until the operator runs `scripts/setup_doc_sync.sh`; do not run it for them |

## Hand-off to Jira

Two URLs feed the Jira Feature ticket:

- **`PRD_{slug}.md`** → the Jira Feature's **Spec Reference** field (profile `fields.spec_reference`), the load-bearing field for downstream Teams comms.
- **`press-release-internal.md`** → an "Internal Press Release" link in the Feature's description body.

`/project:ship-it` Phase 7 option (a) runs the same `urls --json` lookup. For manual use: copy the URLs from the table and paste into `/jira:create --feature`.

## Related Skills

- **workflow-doc-sync**: Reference for the low-level doc_sync maintenance CLI (operator-run only)
- **prd-creation**: Creates PRDs that live in packages
- **ship-it / build**: Upstream workflows that create the package artifacts
- **workflow-jira-home**: Consumes the Spec Reference URL when drafting the Jira Feature
