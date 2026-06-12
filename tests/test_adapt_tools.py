"""Tests for the Adapt hard scope gate (Task 8).

Two layers under test:
  1. scripts/adapt_tools.py - the pure path-confinement logic
     (fairway_paths / is_in_fairway) and the ADAPT_ALLOWED_TOOLS allowlist.
  2. scripts/hooks/adapt_fairway_guard.py - the PreToolUse guard script that
     reaches spawned subagents (invoked here with synthetic stdin payloads;
     no real `claude` needed).

These are deterministic. The empirical `claude` spike is recorded in
docs/plans/notes/adapt-scope-gate-spike.md, not here.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import adapt_tools  # noqa: E402

PM_OS_DIR = adapt_tools.PM_OS_DIR
GUARD = os.path.join(PM_OS_DIR, "scripts", "hooks", "adapt_fairway_guard.py")


# --- fairway_paths -----------------------------------------------------------

def test_fairway_paths_are_exactly_the_allowed_roots():
    assert adapt_tools.fairway_paths() == [
        "scripts/workers",
        "scripts/adapters",
        "ui/task-board/cardtypes/registry.json",
        "judge/rubrics",
        "datasets/adaptations",
    ]


# --- is_in_fairway: allowed --------------------------------------------------

@pytest.mark.parametrize("rel", [
    "scripts/workers/x.md",
    "scripts/adapters/ecommerce/shopify.py",
    "ui/task-board/cardtypes/registry.json",
    "judge/rubrics/x.yaml",
    "datasets/adaptations/x.md",
])
def test_is_in_fairway_true_for_factory_surfaces(rel):
    assert adapt_tools.is_in_fairway(rel) is True


def test_is_in_fairway_true_for_absolute_in_fairway_path():
    abs_path = os.path.join(PM_OS_DIR, "scripts", "workers", "deep", "nested.md")
    assert adapt_tools.is_in_fairway(abs_path) is True


# --- is_in_fairway: denied ---------------------------------------------------

@pytest.mark.parametrize("rel", [
    "ui/task-board/index.html",
    "docs/reference/invariants.md",
    "scripts/task_server.py",
    "scripts/adapt_tools.py",
])
def test_is_in_fairway_false_for_engine_core(rel):
    assert adapt_tools.is_in_fairway(rel) is False


def test_is_in_fairway_defeats_dotdot_escape():
    assert adapt_tools.is_in_fairway("scripts/workers/../../etc/passwd") is False


def test_is_in_fairway_defeats_absolute_escape():
    assert adapt_tools.is_in_fairway("/etc/passwd") is False


def test_is_in_fairway_prefix_sibling_not_matched():
    # scripts/workers_evil must NOT be treated as inside scripts/workers.
    assert adapt_tools.is_in_fairway("scripts/workers_evil/x.md") is False


def test_is_in_fairway_registry_sibling_not_matched():
    # The registry is a single FILE root - a sibling file must not match it.
    assert adapt_tools.is_in_fairway("ui/task-board/cardtypes/registry.json.bak") is False
    assert adapt_tools.is_in_fairway("ui/task-board/cardtypes/other.json") is False


def test_is_in_fairway_handles_empty_and_none():
    assert adapt_tools.is_in_fairway("") is False
    assert adapt_tools.is_in_fairway(None) is False


# --- ADAPT_ALLOWED_TOOLS -----------------------------------------------------

def test_allowlist_has_unrestricted_read_search():
    for t in ("Read", "Grep", "Glob"):
        assert t in adapt_tools.ADAPT_ALLOWED_TOOLS


def test_allowlist_has_path_scoped_writes_for_every_fairway_root():
    tools = adapt_tools.ADAPT_ALLOWED_TOOLS
    assert "Write(scripts/workers/**)" in tools
    assert "Edit(scripts/workers/**)" in tools
    assert "Write(scripts/adapters/**)" in tools
    assert "Edit(scripts/adapters/**)" in tools
    assert "Write(judge/rubrics/**)" in tools
    assert "Edit(judge/rubrics/**)" in tools
    assert "Write(datasets/adaptations/**)" in tools
    assert "Edit(datasets/adaptations/**)" in tools
    assert "Write(ui/task-board/cardtypes/registry.json)" in tools
    assert "Edit(ui/task-board/cardtypes/registry.json)" in tools


def test_allowlist_has_gate_bash_commands():
    tools = adapt_tools.ADAPT_ALLOWED_TOOLS
    assert "Bash(python3 -m pytest:*)" in tools
    assert "Bash(python3 scripts/card_schema.py:*)" in tools
    assert "Bash(python3 scripts/portability_gate.py:*)" in tools


def test_allowlist_has_git_inspect_and_commit_commands():
    tools = adapt_tools.ADAPT_ALLOWED_TOOLS
    for sub in ("add", "commit", "status", "log", "show", "diff",
                "revert", "rev-parse", "rev-list"):
        assert f"Bash(git {sub}:*)" in tools


def test_allowlist_has_subagent_dispatch_tools():
    assert "Agent" in adapt_tools.ADAPT_ALLOWED_TOOLS
    assert "Task" in adapt_tools.ADAPT_ALLOWED_TOOLS


def test_allowlist_has_local_task_cli():
    assert "Bash(./scripts/task.sh:*)" in adapt_tools.ADAPT_ALLOWED_TOOLS


def test_allowlist_has_qmd_read_tools():
    for t in ("mcp__qmd__query", "mcp__qmd__get",
              "mcp__qmd__multi_get", "mcp__qmd__status"):
        assert t in adapt_tools.ADAPT_ALLOWED_TOOLS


def test_allowlist_excludes_broad_bash_and_mcp_wildcards():
    tools = adapt_tools.ADAPT_ALLOWED_TOOLS
    assert "Bash" not in tools
    assert "Bash(*)" not in tools
    assert "mcp__*" not in tools
    # And no bare unscoped Write/Edit (defense in depth alongside the hook).
    assert "Write" not in tools
    assert "Edit" not in tools


# --- the PreToolUse guard script (synthetic payloads) ------------------------

def _run_guard(payload):
    """Run the guard with a synthetic PreToolUse payload on stdin.

    Returns (returncode, stdout, stderr).
    """
    proc = subprocess.run(
        [sys.executable, GUARD],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _is_deny(returncode, stdout):
    """A deny is signalled either by exit 2 OR by a deny permissionDecision."""
    if returncode == 2:
        return True
    try:
        out = json.loads(stdout or "{}")
    except ValueError:
        return False
    decision = (out.get("hookSpecificOutput") or {}).get("permissionDecision")
    return decision == "deny" or out.get("decision") == "block"


def test_guard_allows_write_inside_fairway():
    rc, out, err = _run_guard({
        "tool_name": "Write",
        "tool_input": {"file_path": "scripts/workers/new_worker.md"},
    })
    assert rc == 0
    assert not _is_deny(rc, out)


def test_guard_denies_write_outside_fairway():
    rc, out, err = _run_guard({
        "tool_name": "Write",
        "tool_input": {"file_path": "docs/reference/invariants.md"},
    })
    assert _is_deny(rc, out)


def test_guard_denies_write_to_tmp():
    rc, out, err = _run_guard({
        "tool_name": "Write",
        "tool_input": {"file_path": "/tmp/escape.txt"},
    })
    assert _is_deny(rc, out)


def test_guard_denies_edit_outside_fairway():
    rc, out, err = _run_guard({
        "tool_name": "Edit",
        "tool_input": {"file_path": "ui/task-board/index.html"},
    })
    assert _is_deny(rc, out)


def test_guard_denies_multiedit_outside_fairway():
    rc, out, err = _run_guard({
        "tool_name": "MultiEdit",
        "tool_input": {"file_path": "scripts/task_server.py"},
    })
    assert _is_deny(rc, out)


def test_guard_denies_dotdot_escape():
    rc, out, err = _run_guard({
        "tool_name": "Write",
        "tool_input": {"file_path": "scripts/workers/../../etc/passwd"},
    })
    assert _is_deny(rc, out)


def test_guard_allows_non_write_tools():
    # Read/Grep/Bash are governed by the allowlist, not this write guard.
    rc, out, err = _run_guard({
        "tool_name": "Read",
        "tool_input": {"file_path": "docs/reference/invariants.md"},
    })
    assert rc == 0
    assert not _is_deny(rc, out)


def test_guard_allows_edit_to_registry_json():
    rc, out, err = _run_guard({
        "tool_name": "Edit",
        "tool_input": {"file_path": "ui/task-board/cardtypes/registry.json"},
    })
    assert rc == 0
    assert not _is_deny(rc, out)


def test_guard_handles_missing_file_path_gracefully():
    # A write with no file_path can't be confined - block it conservatively.
    rc, out, err = _run_guard({
        "tool_name": "Write",
        "tool_input": {},
    })
    assert _is_deny(rc, out)


def test_guard_handles_malformed_stdin():
    proc = subprocess.run(
        [sys.executable, GUARD],
        input="not json at all",
        capture_output=True,
        text=True,
    )
    # Malformed payload must not allow a write through; non-zero or deny.
    assert proc.returncode != 0 or _is_deny(proc.returncode, proc.stdout)


# --- the PreToolUse guard script: Bash file-write hole (regression) ----------
#
# An allowlisted Bash command runs in a shell, so it can redirect output or use
# a write-flag to write to an arbitrary path the file-write guard never sees.
# The guard must DENY any Bash command that can write to a file, while still
# allowing the read-only / gate / git / task-CLI commands the harness needs.

def test_guard_denies_bash_git_diff_output_flag():
    # `git diff --output=<path>` writes the diff to a file - the live hole.
    rc, out, err = _run_guard({
        "tool_name": "Bash",
        "tool_input": {"command": "git diff --output=/tmp/_probe HEAD~1 HEAD"},
    })
    assert rc == 2
    assert _is_deny(rc, out)


def test_guard_denies_bash_redirection_to_engine_core():
    # Shell redirection `>` can overwrite anything, e.g. an invariant doc.
    rc, out, err = _run_guard({
        "tool_name": "Bash",
        "tool_input": {
            "command": "git status > /Users/x/docs/reference/invariants.md"
        },
    })
    assert rc == 2
    assert _is_deny(rc, out)


def test_guard_denies_bash_tee_pipe():
    # `| tee <path>` likewise writes to a file.
    rc, out, err = _run_guard({
        "tool_name": "Bash",
        "tool_input": {"command": "git show HEAD:foo | tee /etc/x"},
    })
    assert rc == 2
    assert _is_deny(rc, out)


def test_guard_allows_bash_commit_with_quoted_redirection_char():
    # A `>` INSIDE a quoted commit message is data, not a redirection - allow.
    rc, out, err = _run_guard({
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -m "fixes > bug"'},
    })
    assert rc == 0
    assert not _is_deny(rc, out)


def test_guard_allows_bash_git_add():
    rc, out, err = _run_guard({
        "tool_name": "Bash",
        "tool_input": {"command": "git add scripts/workers/x.md"},
    })
    assert rc == 0
    assert not _is_deny(rc, out)


def test_guard_allows_bash_pytest():
    rc, out, err = _run_guard({
        "tool_name": "Bash",
        "tool_input": {"command": "python3 -m pytest -q"},
    })
    assert rc == 0
    assert not _is_deny(rc, out)


def test_guard_denies_bash_with_no_command():
    # A Bash call with no command string can't be inspected - deny (fail closed).
    rc, out, err = _run_guard({
        "tool_name": "Bash",
        "tool_input": {},
    })
    assert rc == 2
    assert _is_deny(rc, out)


def test_guard_denies_bash_unbalanced_quote():
    # An unparseable command (shlex error) must fail closed.
    rc, out, err = _run_guard({
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -m "oops'},
    })
    assert rc == 2
    assert _is_deny(rc, out)


# --- the PreToolUse guard script: fail-closed on bad payload shapes ----------

def test_guard_denies_non_dict_top_level_payload():
    # A JSON list/scalar top level must DENY with exit 2, not crash with exit 1
    # (exit 1 is a non-blocking hook error => the write would proceed).
    proc = subprocess.run(
        [sys.executable, GUARD],
        input=json.dumps([1, 2, 3]),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2
    assert _is_deny(proc.returncode, proc.stdout)
