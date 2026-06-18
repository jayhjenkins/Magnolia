#!/usr/bin/env python3
"""PreToolUse guard: bound a headless onboarding session.

This is the REAL enforcement of the onboarding fairway. It is registered as a
PreToolUse hook via scripts/hooks/onboard_settings.json and passed to the
onboarding session with `claude --settings scripts/hooks/onboard_settings.json`.

The onboarding agent is deliberately HIGH-PRIVILEGE - the OPPOSITE of the
locked-down chat panel. It writes the profile, runs installers, runs auth,
copies datasets, and spins up the board. So unlike the Adapt fairway (four tiny
factory surfaces), this fairway is BROAD: writes are allowed anywhere under the
Magnolia repo. The bound it enforces is "don't reach OUTSIDE the repo to clobber
the user's machine, and never run an obviously destructive command." Same power
as `onboard me` today, just made structural.

Why a hook and not just --allowedTools: a PreToolUse hook fires for EVERY tool
call, including those made by spawned subagents - so it is the only mechanism we
can trust to reach the full onboarding flow. The broad ONBOARD_ALLOWED_TOOLS
allowlist (in onboard_runner) is defense in depth on top.

Two checks, both fail-closed:
  1. File-write tools (Write / Edit / MultiEdit / NotebookEdit): DENY when the
     resolved target path is outside the repo.
  2. Bash: DENY only obviously destructive commands; installers/auth/cp/python
     are ALLOWED.

Output: emits BOTH supported block signals (structured JSON on stdout + exit 2
with reason on stderr), exactly like adapt_fairway_guard. The fairway logic is
imported from the single source of truth (onboard_tools), mirroring how
adapt_fairway_guard imports adapt_tools.

Pure-stdlib, ASCII-only (invariant #8), no hardcoded identity (invariant #1).
"""
import json
import os
import sys

# Make the sibling scripts/ dir importable so we reuse the single source of
# truth for the fairway (onboard_tools) rather than re-deriving it.
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import onboard_tools  # noqa: E402

_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_PATH_KEYS = ("file_path", "path", "notebook_path")


def _target_path(tool_input):
    if not isinstance(tool_input, dict):
        return None
    for key in _PATH_KEYS:
        value = tool_input.get(key)
        if value:
            return str(value)
    return None


def _deny(reason):
    decision = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(decision))
    print(reason, file=sys.stderr)
    sys.exit(2)


def _allow():
    sys.exit(0)


def _guard_bash(tool_input):
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not command:
        _deny("onboard fairway: Bash call had no command string; denying to stay safe.")
        return
    if onboard_tools.bash_is_destructive(command):
        _deny(
            "onboard fairway: blocked Bash command '"
            + command
            + "' - it is obviously destructive (rm -rf of a root/home target, "
            + "mkfs, dd to a device, or a fork bomb). Onboarding never needs to "
            + "do that. If this was intended, run it yourself in a terminal."
        )
        return
    _allow()


def _guard_write(tool_name, tool_input):
    target = _target_path(tool_input)
    if not target:
        _deny(
            "onboard fairway: "
            + tool_name
            + " call had no resolvable file path; denying to stay inside the repo."
        )
        return
    if onboard_tools.is_in_fairway(target):
        _allow()
        return
    _deny(
        "onboard fairway: blocked "
        + tool_name
        + " to '"
        + target
        + "' - it is outside the Magnolia repo. Onboarding writes the profile, "
        + "datasets, config, and board, all of which live inside the repo. It "
        + "must not write to the user's wider machine."
    )


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        _deny("onboard fairway: unparseable PreToolUse payload; denying to stay safe.")
        return
    if not isinstance(payload, dict):
        _deny("onboard fairway: malformed PreToolUse payload (not an object); denying to stay safe.")
        return

    tool_name = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}

    if tool_name == "Bash":
        _guard_bash(tool_input)
        return
    if tool_name in _WRITE_TOOLS:
        _guard_write(tool_name, tool_input)
        return

    # Any other tool is governed by the allowlist; let it through.
    _allow()


def _main_failclosed():
    """Run main(); deny on ANY unexpected exception (never exit 1, which would
    be a non-blocking hook error => the call would proceed)."""
    try:
        main()
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - any unexpected failure must deny
        _deny("onboard fairway: unexpected guard error; denying to stay safe.")


if __name__ == "__main__":
    _main_failclosed()
