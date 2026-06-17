# Changelog

All notable changes to Magnolia are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- **Windows compatibility — Python PATH gotcha documented** (`docs/INSTALL-windows.md`): new section
  explains the symptom (agents exit code 1 silently), the test (`! python --version`), and a
  remedies table for every common root cause.
- **`doctor.py` — `python_interpreter` capability check**: the Doctor now detects whether Python is
  reachable from Git Bash (the #1 silent failure on Windows) and surfaces a clear remedy if missing.
- **Jira Severity field support** (`jira_publish.py`, `workflow-jira-home` skill): `JIRA_SEVERITY`
  is now parsed from draft blocks and written to `customfield_10269`. Required for Bug, Regression
  Defect, and Work Item Defect types; Jira rejects those types without it.
- **`JIRA_COMPONENT_ID` per-ticket override** (`jira_publish.py`): draft blocks can now set a
  component ID that overrides the profile default (e.g. Apollo Action Items = 10048).
- **Board cancel action** (`task_server.py`, `core.js`, `tasks.js`): new `POST /api/tasks/{id}/cancel`
  endpoint; confirm dialog now accepts an optional reason textarea.
- **Board "In Review" status column** (`board.js`): agent and collab lanes now surface an
  `in-review` group between Done and In Progress.
- **`jira_publish.py` hot-reload on publish** (`task_server.py`): `importlib.reload(jira_publish)`
  before each publish so field changes take effect without restarting the server.

### Fixed
- **Agents failing silently on `task.sh update --description`**: `task_cli.py` was missing the
  `--description` flag; `task_lib.py` had no body-replacement logic. Both are now implemented.
  `ticket-creator` worker rewritten to use the `Edit` tool instead of a heredoc.
- **`feed_guard.py` NameError crash on macOS**: `disable()` used `result` before assigning it.
  Fixed; `launchctl` calls now guarded by `platform_lib.os_kind() == "darwin"` so the function
  is a safe no-op on Windows.
- **`granola_sync.py` bare `"claude"` string**: replaced with `platform_lib.resolve_claude()` so
  Claude is found in Windows-specific install locations (VS Code/Cursor extensions, npm global,
  Anthropic desktop).
- **`persist_lib.py` PowerShell path quoting**: `render_scheduled_task()` now uses single-quoted
  PowerShell string literals so paths with spaces (e.g. `C:\Users\Eddie Key\...`) don't break the
  generated scheduled-task command.
- **`session-start.sh` `python3`-only invocation**: replaced with `python3 || python` fallback so
  the operator display name resolves correctly on Windows Git Bash where only `python` exists.
- **`task_dispatch.py` file handle leak**: `open(output_file, "wb")` moved inside the `try` block;
  `except FileNotFoundError` now closes the handle if it was opened.
- **`transcript_post.py` anonymous file handles in Popen**: extracted to named variables
  (`_qmd_log`, `_extract_log`) and closed immediately after spawning, preventing GC-dependent leaks.

### Changed
- **macOS-only shell scripts labeled**: `run_task_server.sh`, `install_granola_sync.sh`,
  `setup_doc_sync.sh`, `qmd-setup.sh`, `qmd-nightly-update.sh` now carry a prominent header
  block so Windows users know not to run them.
- **`workflow-landing-page-creator` skill**: validation Python block changed from stdin heredoc
  (`python3 - <<'PY'`) to a temp-file approach with `python3 || python` fallback — the stdin
  form silently fails on Windows Git Bash.
