---
name: workflow-doc-sync
description: Use when someone asks how Markdown-to-Word sync works, wants a Word copy of a document, or asks about doc_sync - explains that publishing happens only from the board editor's 3-dot menu and that the doc_sync CLI is operator-run maintenance agents must not use to push
triggers:
  - doc sync
  - convert markdown to Word
  - Word document sync
  - Open in Word
  - doc_sync status
type: workflow
---

# Document Sync Skill

Reference for how local markdown relates to Word copies in the OneDrive sync folder.

```
Local MD files -> pandoc -> .docx in OneDrive sync folder -> SharePoint
```

## The rule: the editor menu is the only publish path

Word is a tertiary surface, touched only on purpose. The **only** place the system pushes a Word doc is the board's markdown editor 3-dot menu:

- **Publish to Word** - shown when no Word copy exists yet. The first-ever publish asks one plain-language confirm.
- **Sync with Word** - shown once the Word copy exists. Overwrites the .docx from markdown.

**Agents, skills, commands, and workers must never push to Word/OneDrive.** Do not run `sync-one`, `sync-folder`, `sync-all`, `sync-back`, or `resolve`. Task completion no longer syncs anything. When a PM wants a Word copy, tell them to open the file in the board editor and use the 3-dot menu.

## What agents may run (read-only)

```bash
# Word Online URLs for .md files in a folder whose Word copy already exists
python3 scripts/doc_sync.py urls datasets/product/packages/2026/example/ --json
# -> {"folder": "...", "files": [{"file": "...", "url": "..."}]}

# Sync state report
python3 scripts/doc_sync.py status
```

## Operator maintenance CLI (not for agents)

The `doc_sync.py` push/pull subcommands remain as low-level maintenance tools the operator runs themselves, by hand, in a terminal:

| Subcommand | What it does |
|---|---|
| `sync-one <md>` | Render one .md to its .docx |
| `sync-folder <dir> [--json]` | Render every .md in a folder |
| `sync-all` | Render every tracked file |
| `sync-back <docx>` | Pull Word edits back into markdown |
| `resolve <md>` | Push the local version after a manual conflict merge |

If an operator asks an agent to run one of these, point them to the editor 3-dot menu first; these are for repair and bulk maintenance, not the normal flow.

## Path Mapping

| Local | OneDrive |
|-------|----------|
| `datasets/product/packages/2026/X/PRD_X.md` | `{onedrive}/PM-OS/product/packages/2026/X/PRD_X.docx` |
| `datasets/strategy/memos/X.md` | `{onedrive}/PM-OS/strategy/memos/X.docx` |

Rule: strip `datasets/` prefix, mirror path, change `.md` to `.docx`. A document "has a Word copy" when that mapped .docx is on disk.

## Conflict Resolution

When both local and remote files change since last sync:
1. Neither file is overwritten
2. Backup copies are created with `_CONFLICT_{timestamp}` suffix
3. Conflict logged to `logs/doc_sync_conflicts.log`
4. The operator runs `resolve <path>` after a manual merge

## Watcher Daemon (retired)

The `doc_sync_watcher.py` file watcher is retired: it exits with a message, and `scripts/setup_doc_sync.sh` no longer installs its launchd job. Nothing syncs in the background.

## Configuration

Edit `scripts/sync_config.yaml` to change:
- `onedrive_root` - OneDrive sync folder path
- `sync_paths` - Which file patterns map to Word
- `sync_exclude` - Which patterns to skip

## Setup

The operator runs `scripts/setup_doc_sync.sh` for first-time setup (installs pandoc, writes config).

## Integration with Task System

Completing a task with `--output` does not create a Word copy. The task card shows an "Open in Word" link only when the output's .docx already exists (created from the editor 3-dot menu).
