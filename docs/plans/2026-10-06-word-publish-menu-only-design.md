# Word publish: menu-only (Word as a tertiary surface)

**Date:** 2026-10-06 · **Status:** approved · **Merge:** to `main` when green

## Problem
A Word (.docx) copy lands in OneDrive almost every time a task finishes. `task_lib.complete_task`
and `task_cli agent:complete` stamp `sharepoint_path`/`sharepoint_url` and fire a background
`doc_sync.py sync-one`. The server also computes an "Open in Word" URL for every task output,
even when no .docx exists. Word should be a tertiary surface that is only touched on purpose.

## Decision
The **only** place the system pushes a Word doc is the markdown editor's 3-dot menu:
- **Publish to Word** when no .docx exists yet for this output.
- **Sync with Word** once it exists. This always overwrites the .docx from markdown, silently.
- The first-ever publish (integration-level) gets one plain-language Tier-2 confirm (invariant #5),
  recorded as `integrations.yaml doc_sync.confirmed: true`.

"Exists" = the mapped .docx file is on disk (not the manifest, which carries stale paths).

Removed push paths (operator chose "strict: menu only, everywhere"):
| Path | Change |
|---|---|
| `task_lib.complete_task`, `task_cli agent:complete` | no sharepoint fields, no `_trigger_doc_sync` |
| `task_server._enrich_sharepoint_url` | only adds a URL when the .docx exists |
| `adapters/messaging/m365.py` Teams attachments | reuse an existing Word URL, else degrade to inline link; never `sync_one` |
| `/project:ship-it` Phase 7 option (a) | look up existing Word URLs (no push) |
| `workflow-publish-package` / `/project:publish-package` | becomes a lookup of existing Word URLs |
| `doc_sync_watcher.py` + its launchd install in `setup_doc_sync.sh` | retired (exits with a message; setup no longer installs it) |

Kept: email attachments render a temp .docx (never written to OneDrive), task-delete cleanup,
the `doc_sync.py` CLI as a low-level maintenance tool.

## Build contract (meta-scope-extension)
| Surface | Decision | Seam | Gate |
|---|---|---|---|
| adapter | reuse (existing `doc_sync` + `messaging/m365`), no new family | `scripts/doc_sync.py`, `scripts/adapters/messaging/m365.py` | `pytest` |
| worker | none | - | - |
| card | none (registry untouched; Word tile already keys on server fields) | `ui/task-board/js/tasks.js`, `board.js` unchanged | `card_schema.py` |
| platform / UI | extend | `task_server.py` route, `ui/task-board/js/markdown-editor.js` menu | `pytest`, `portability_gate.py`, live e2e |

Standing item: runtime output ASCII-safe (hyphen, not em-dash; ASCII quotes).

## API contract
- `doc_sync.word_status(md_path) -> {"exists": bool, "url": str|None, "docx_path": str|None}`; never raises,
  never writes; unconfigured doc_sync -> `{"exists": False, "url": None, "docx_path": None}`.
- `doc_sync.py urls <dir> --json` -> same shape as `sync-folder --json` but read-only (only files whose .docx exists).
- `GET /api/tasks/{id}/output` adds `"word": word_status(...)`.
- `POST /api/tasks/{id}/output/word` body `{"confirm": bool}`:
  - doc_sync unconfigured -> 400 `{"error": "..."}`
  - `doc_sync.confirmed` not true and `confirm` falsy -> 409 `{"needs_confirmation": true, "message": "..."}`
  - `confirm` true -> `profile_lib.set_integration_confirmed("doc_sync", True)` then push
  - success -> 200 `{"ok": true, "action": "published"|"synced", "word": word_status(...)}`; also stamps
    the task's `sharepoint_path`/`sharepoint_url` so the card's Word tile appears.

## Plan
1. Backend (TDD): `word_status`, `urls` CLI, strip auto-push in task_lib/task_cli, gate enrichment, new route, m365 Teams no-push.
2. UI: menu item with Publish/Sync label from `word`, confirm on 409 via `confirmAction`, toast + "Open in Word" item when exists.
3. Skills/commands/watcher: ship-it Phase 7, publish-package, workflow-doc-sync, watcher retirement, setup script.
4. Gates + live e2e on the board.
