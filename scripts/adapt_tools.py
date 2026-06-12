"""The Adapt build session's hard scope gate (Task 8).

This is the security crux of the Adapt feature: an Adapt build session is a
headless `claude -p` run that can edit engine files, but it must be PHYSICALLY
confined to the four factory surfaces (plus the adaptations store) so it can
never touch the top nav, board chrome, engine core, or docs/reference.

Three layers of confinement, belt-and-suspenders:
  1. The PreToolUse guard hook (scripts/hooks/adapt_fairway_guard.py, registered
     via scripts/hooks/adapt_settings.json) - the PRIMARY enforcement. A
     PreToolUse hook fires for EVERY tool call, including those made by spawned
     subagents, so it is the only mechanism we can trust to reach the
     subagent-driven build loop. It denies any Write/Edit/MultiEdit whose
     resolved target is outside the fairway.
  2. The path-scoped ADAPT_ALLOWED_TOOLS allowlist below - defense in depth on
     top of the hook. It also withholds every broad/external-write tool.
  3. The harness prose (scripts/adapt_harness.py) - the third, advisory layer.

This module is PURE: no subprocess, no file writes, no claude. It is modelled on
chat_runner.CHAT_ALLOWED_TOOLS for house style - same Tier-2 spirit (invariant
#5): never GRANT a tool that reaches the outside world, and scope Bash to a
named set of read-only / gate / local subcommands. ASCII-only (invariant #8).
No hardcoded identity (invariant #1) - the fairway is structural, not personal.
"""
import os

# Path normalization here is pure os.path (realpath / sep / join), which is
# fully portable across macOS and Windows - no OS branch is needed, so this
# module does NOT reach for the platform_lib seam (it would be a no-op import).

# Repo root - the cwd `claude` runs in, mirroring chat_runner.PM_OS_DIR. Every
# fairway path is resolved relative to this, then prefix-checked, so a `..`
# escape that climbs out of the repo can never be judged in-fairway.
PM_OS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The four factory surfaces (repo-relative), plus the adaptations store. These
# are the ONLY write roots an Adapt build session may touch:
#   - scripts/workers       : meta-create-worker output (a worker is one .md)
#   - scripts/adapters       : meta-create-adapter output (<family>/<provider>.py)
#   - ui/task-board/cardtypes/registry.json : meta-create-card-type output (a
#                              SINGLE FILE - the declarative card registry)
#   - judge/rubrics          : per-deliverable judge rubrics (a future surface;
#                              the dir need not exist yet - this is a path check)
#   - datasets/adaptations   : the adaptation store the runner writes manifests to
#
# registry.json is a single FILE root; the other four are DIRECTORY roots.
# is_in_fairway distinguishes the two so a sibling file (registry.json.bak)
# can't sneak in and a sibling dir (scripts/workers_evil) can't match
# scripts/workers.
_REGISTRY_REL = "ui/task-board/cardtypes/registry.json"

_DIR_ROOTS = (
    "scripts/workers",
    "scripts/adapters",
    "judge/rubrics",
    "datasets/adaptations",
)


def fairway_paths():
    """Return the allowed write roots (repo-relative), in canonical order.

    The four directory roots plus the single registry.json file root. Used by
    tests, the guard hook, and any caller that needs to name the fairway.
    """
    return [
        "scripts/workers",
        "scripts/adapters",
        "ui/task-board/cardtypes/registry.json",
        "judge/rubrics",
        "datasets/adaptations",
    ]


def _realpath_under_root(path):
    """Resolve `path` to an absolute realpath, anchored under PM_OS_DIR.

    A relative path is joined onto PM_OS_DIR first; an absolute path is used
    as-is. os.path.realpath then collapses any `..` segments and resolves
    symlinks, so the returned path is the true on-disk location regardless of
    how the caller tried to dress it up. Path normalization goes through this
    one helper so the `..`-defeat logic lives in a single place.
    """
    if os.path.isabs(path):
        candidate = path
    else:
        candidate = os.path.join(PM_OS_DIR, path)
    return os.path.realpath(candidate)


def _contains(root_abs, target_abs):
    """True iff target_abs is root_abs itself or sits strictly under it.

    Compares with an os.sep boundary so a prefix match on a SIBLING name
    (scripts/workers_evil vs scripts/workers) is rejected. This is the prefix
    check the spec calls for.
    """
    if target_abs == root_abs:
        return True
    return target_abs.startswith(root_abs + os.sep)


def is_in_fairway(path):
    """True iff `path` resolves inside one of the fairway write roots.

    `path` is resolved via os.path.realpath under PM_OS_DIR (defeating `..`
    escapes and symlinks), then matched against the registry.json file root
    (exact match) OR any of the directory roots (containment with an os.sep
    boundary). Anything else - engine core, board chrome, docs, an absolute path
    outside the repo, a `..` climb - is False. Falsy input is False.
    """
    if not path:
        return False

    target_abs = _realpath_under_root(path)

    # registry.json is a single-FILE root: require an exact realpath match so a
    # sibling (registry.json.bak / other.json) does not slip through.
    registry_abs = _realpath_under_root(_REGISTRY_REL)
    if target_abs == registry_abs:
        return True

    # The directory roots: containment under the resolved root, sep-bounded.
    for rel in _DIR_ROOTS:
        root_abs = _realpath_under_root(rel)
        if _contains(root_abs, target_abs):
            return True

    return False


# --- The Adapt build session's tool allowlist (Tier-2 boundary) --------------
#
# Modelled on chat_runner.CHAT_ALLOWED_TOOLS. This is defense-in-depth ON TOP of
# the PreToolUse guard hook (the hook is primary because it reaches subagents).
# The list DELIBERATELY:
#   - scopes Write/Edit to the fairway roots ONLY (no bare Write / Edit),
#   - scopes Bash to a named set: the green-gate commands, read-only + commit
#     git, and the local task CLI - never plain `Bash` or `Bash(*)`,
#   - EXCLUDES the broad `mcp__*` wildcard (would grant every external-write
#     MCP tool) - only the read-only qmd query/fetch tools are granted,
#   - GRANTS Agent/Task so the subagent-driven build loop can dispatch
#     subagents (those subagents are confined by the PreToolUse hook, which a
#     path-scoped allowlist alone could not guarantee).
#
# To add a tool: prove it cannot write outside the fairway or to the outside
# world. Err toward fewer tools.
ADAPT_ALLOWED_TOOLS = [
    # Unrestricted read / search - the build needs to study the whole engine.
    "Read", "Grep", "Glob",
    # Path-scoped writes: the four factory surfaces only. registry.json is a
    # single file; the rest are directory globs.
    "Write(scripts/workers/**)", "Edit(scripts/workers/**)",
    "Write(scripts/adapters/**)", "Edit(scripts/adapters/**)",
    "Write(judge/rubrics/**)", "Edit(judge/rubrics/**)",
    "Write(datasets/adaptations/**)", "Edit(datasets/adaptations/**)",
    "Write(ui/task-board/cardtypes/registry.json)",
    "Edit(ui/task-board/cardtypes/registry.json)",
    # The three green gates (invariant #2) - the build runs them before commit.
    "Bash(python3 -m pytest:*)",
    "Bash(python3 scripts/card_schema.py:*)",
    "Bash(python3 scripts/portability_gate.py:*)",
    # Git: read-only inspection PLUS the commit/revert path the factory uses.
    # commit/revert are local-only (no push) - they cannot reach outside.
    "Bash(git add:*)", "Bash(git commit:*)", "Bash(git status:*)",
    "Bash(git log:*)", "Bash(git show:*)", "Bash(git diff:*)",
    "Bash(git revert:*)", "Bash(git rev-parse:*)", "Bash(git rev-list:*)",
    # Subagent dispatch - the subagent-driven-development loop. The PreToolUse
    # hook confines the writes these subagents make.
    "Agent", "Task",
    # The local task CLI - thin wrapper over task_cli.py (local task-file
    # mutation only; no external-write path), same exemption as the chat panel.
    "Bash(./scripts/task.sh:*)",
    # Read-only semantic search over the local corpus (qmd query/fetch only).
    "mcp__qmd__query", "mcp__qmd__get", "mcp__qmd__multi_get", "mcp__qmd__status",
]
