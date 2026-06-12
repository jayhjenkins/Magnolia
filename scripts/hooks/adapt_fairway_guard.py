#!/usr/bin/env python3
"""PreToolUse guard: confine an Adapt build session's writes to the fairway.

This is the PRIMARY enforcement of the Adapt hard scope gate (Task 8). It is
registered as a PreToolUse hook via scripts/hooks/adapt_settings.json and passed
to the build session with `claude --settings scripts/hooks/adapt_settings.json`.

Why a hook and not just a path-scoped --allowedTools: a PreToolUse hook fires
for EVERY tool call - top-level AND those made by spawned subagents - so it is
the only mechanism we can trust to reach the subagent-driven build loop. The
path-scoped allowlist (adapt_tools.ADAPT_ALLOWED_TOOLS) is defense in depth on
top of this.

Contract (Claude Code PreToolUse hook):
  - stdin: a JSON object with at least `tool_name` and `tool_input`.
  - We act only on the file-writing tools (Write / Edit / MultiEdit / write
    variants). For those, we resolve the target file path and DENY when it is
    not is_in_fairway. Everything else is allowed (the allowlist governs which
    non-write tools exist at all).

Output: we emit BOTH supported block signals so the guard works across CLI
versions -
  - the structured JSON decision on stdout:
      {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                              "permissionDecision": "deny",
                              "permissionDecisionReason": "..."}}
  - AND a non-zero exit (2) with the reason on stderr (the older contract,
    where exit 2 = block and stderr = the reason fed back to the model).
On allow we print nothing and exit 0.

Pure-stdlib, ASCII-only (invariant #8), no hardcoded identity (invariant #1).
"""
import json
import os
import shlex
import sys

# Make the sibling scripts/ dir importable so we can reuse the single source of
# truth for the fairway (adapt_tools.is_in_fairway) rather than re-deriving it.
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import adapt_tools  # noqa: E402

# The file-write tools - the ones this guard confines by target PATH. Names
# match the Claude Code tool names (case-sensitive).
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# Where a write tool carries its target path, in probe order.
_PATH_KEYS = ("file_path", "path", "notebook_path")

# Bash is allowlisted to a named set of read-only / gate / git / task-CLI
# commands - but a shell can redirect output or use a write-flag to write to an
# arbitrary path the file-write guard above never sees. So we ALSO inspect Bash
# and deny any command that can write to a file. None of the allowlisted
# commands legitimately need to write to a file via the shell.
#
# Exact-token denials: a shell redirection operator or `tee`.
_BASH_DENY_TOKENS = {">", ">>", ">|", "&>", "&>>", "tee"}
# Exact-token write-flag denials (e.g. git --output / -o).
_BASH_DENY_FLAGS = {"--output", "--output-file", "-o"}
# Prefixed write-flag denials (the `=form`: --output=PATH).
_BASH_DENY_FLAG_PREFIXES = ("--output=", "--output-file=")


def _target_path(tool_input):
    """Pull the target file path from a write tool's input dict, or None."""
    if not isinstance(tool_input, dict):
        return None
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if value:
            return str(value)
    return None


def _deny(reason):
    """Emit both block signals and exit non-zero (2)."""
    decision = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    # Structured decision on stdout (modern contract).
    print(json.dumps(decision))
    # Reason on stderr + exit 2 (older contract: exit 2 == block).
    print(reason, file=sys.stderr)
    sys.exit(2)


def _allow():
    """Say nothing, exit 0 - the tool call proceeds."""
    sys.exit(0)


def _bash_writes_to_file(token):
    """True iff a single shlex token is a file-write redirection or write-flag.

    Tokens are matched AFTER shlex.split, so a `>` that lived inside a quoted
    string (e.g. the commit message 'fixes > bug' yields one token
    'fixes > bug') is NOT equal to '>' and does NOT start with '>', and is
    correctly judged harmless here.
    """
    if token in _BASH_DENY_TOKENS:
        return True
    if token.startswith(">"):  # e.g. `>/tmp/x` glued to its target
        return True
    if token in _BASH_DENY_FLAGS:
        return True
    for prefix in _BASH_DENY_FLAG_PREFIXES:
        if token.startswith(prefix):
            return True
    return False


def _guard_bash(tool_input):
    """Deny any Bash command that can write to a file; allow otherwise.

    The Bash tool_input carries the shell command under `command`. We parse it
    with shlex and deny on any redirection / `tee` / write-flag token. A missing
    command or an unparseable command (unbalanced quotes etc.) fails CLOSED.
    """
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not command:
        _deny(
            "adapt scope gate: Bash call had no command string; denying to stay "
            "inside the fairway."
        )
        return

    try:
        tokens = shlex.split(command)
    except ValueError:
        _deny(
            "adapt scope gate: could not parse Bash command (unbalanced quotes?); "
            "denying to stay safe."
        )
        return

    for token in tokens:
        if _bash_writes_to_file(token):
            _deny(
                "adapt scope gate: blocked Bash command '"
                + command
                + "' - it can write to a file (redirection / tee / --output), "
                + "which would bypass the fairway. Allowlisted Bash commands "
                + "must only read or write to stdout. To write a file, use Write/"
                + "Edit into the factory surfaces."
            )
            return

    _allow()


def _guard_write(tool_name, tool_input):
    """Confine a file-write tool to the fairway by its target path."""
    target = _target_path(tool_input)
    if not target:
        # A write tool with no resolvable path can't be confined - deny rather
        # than guess.
        _deny(
            "adapt scope gate: "
            + tool_name
            + " call had no resolvable file path; denying to stay inside the fairway."
        )
        return

    if adapt_tools.is_in_fairway(target):
        _allow()
        return

    fairway = ", ".join(adapt_tools.fairway_paths())
    _deny(
        "adapt scope gate: blocked "
        + tool_name
        + " to '"
        + target
        + "' - it is outside the Adapt fairway. Adapt build sessions may only "
        + "write to the factory surfaces ("
        + fairway
        + "). To change anything else, run Claude Code natively in the Magnolia "
        + "folder."
    )


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        # A payload we can't parse must not let a write/Bash call through. Deny.
        _deny("adapt scope gate: unparseable PreToolUse payload; denying to stay safe.")
        return

    # Fail CLOSED on any non-dict top-level shape (list/scalar/null). Calling
    # .get on these would raise AttributeError -> exit 1, which is a NON-blocking
    # hook error (the call would proceed). Deny explicitly instead.
    if not isinstance(payload, dict):
        _deny("adapt scope gate: malformed PreToolUse payload (not an object); denying to stay safe.")
        return

    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}

    # Bash: deny any command that can write to a file (redirection / tee /
    # write-flag) - the allowlist already governs WHICH bash commands exist.
    if tool_name == "Bash":
        _guard_bash(tool_input)
        return

    # File-write tools: confine by target path.
    if tool_name in _WRITE_TOOLS:
        _guard_write(tool_name, tool_input)
        return

    # Any other tool is not this guard's concern - the allowlist already decides
    # whether it exists. Let it through.
    _allow()


def _main_failclosed():
    """Run main(); deny on ANY unexpected exception (never crash with exit 1).

    A SystemExit (raised by _allow/_deny via sys.exit) must pass straight
    through; any OTHER exception is an unexpected crash and must fail CLOSED
    (exit 2 = block) rather than exit 1 (a non-blocking hook error => the call
    would proceed).
    """
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - any unexpected failure must deny
        _deny("adapt scope gate: unexpected guard error; denying to stay safe.")


if __name__ == "__main__":
    _main_failclosed()
