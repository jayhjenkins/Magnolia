"""The onboarding session's fairway logic (pure path + bash confinement).

The onboarding agent is deliberately HIGH-PRIVILEGE - the OPPOSITE of the
locked-down chat panel. It genuinely needs to: write the profile, run installers
(npm install qmd, etc.), run auth (mgc login, otter_auth), copy datasets, and
spin up the board. So unlike the Adapt fairway (which confines writes to four
tiny factory surfaces), this fairway is BROAD: writes are allowed anywhere under
the Magnolia repo (where the profile, datasets, config, and board all live). The
bound it enforces is therefore NOT "stay in the factory" but "don't reach
OUTSIDE the repo to clobber the user's machine, and never run an obviously
destructive command." This is no more dangerous than `onboard me` is today (same
power, UI-driven); the hook just makes the boundary structural rather than
advisory.

This module is PURE: no subprocess, no file writes, no claude. The PreToolUse
guard (scripts/hooks/onboard_fairway_guard.py) imports it as the single source
of truth - mirroring how adapt_fairway_guard imports adapt_tools. ASCII-only
(invariant #8), no hardcoded identity (invariant #1) - the fairway is
structural, not personal.
"""
import os
import re
import shlex

# Repo root - the cwd `claude` runs in (set by chat_runner._spawn). Every write
# path is resolved relative to this, then checked for containment, so a `..`
# escape that climbs out of the repo is rejected.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)


def _realpath_under_root(path):
    """Resolve `path` to an absolute realpath, anchored under PM_OS_DIR.

    A relative path is joined onto PM_OS_DIR first; an absolute path is used
    as-is. os.path.realpath collapses any `..` segments and resolves symlinks.
    """
    if os.path.isabs(path):
        candidate = path
    else:
        candidate = os.path.join(PM_OS_DIR, path)
    return os.path.realpath(candidate)


def is_in_fairway(path):
    """True iff `path` resolves inside the Magnolia repo (the onboarding fairway).

    `path` is resolved via os.path.realpath under PM_OS_DIR (defeating `..`
    escapes and symlinks), then checked for containment under the repo root with
    an os.sep boundary. Anything outside the repo - the user's home, /etc, an
    absolute path elsewhere - is False. Falsy input is False.
    """
    if not path:
        return False
    target_abs = _realpath_under_root(path)
    root_abs = os.path.realpath(PM_OS_DIR)
    if target_abs == root_abs:
        return True
    return target_abs.startswith(root_abs + os.sep)


# Obviously-destructive Bash. We DENY these and allow everything else (the
# allowlist governs which bash commands exist at all; onboarding genuinely needs
# installers/auth/cp/python). These are coarse safety rails against a
# catastrophic mistake, not a sandbox.

def _is_rm_rf_root(tokens):
    """rm -rf (or -fr) targeting / or a home/root-level path."""
    if not tokens:
        return False
    toks = tokens[1:] if tokens[0] == "sudo" else tokens   # catch `sudo rm -rf /`
    if not toks or os.path.basename(toks[0]) != "rm":
        return False
    flags = "".join(t[1:] for t in toks if t.startswith("-") and not t.startswith("--"))
    if not ("r" in flags and "f" in flags):
        return False
    targets = [t for t in toks[1:] if not t.startswith("-")]
    dangerous = {"/", "~", os.path.expanduser("~")}
    for t in targets:
        if t in dangerous or t.rstrip("/*") in ("", "/"):
            return True
        if re.fullmatch(r"/[A-Za-z0-9_.-]+/?\*?", t):   # a top-level absolute dir
            return True
    return False


def _is_mkfs_or_dd_device(tokens):
    """mkfs.* anything, or dd writing to a /dev device."""
    if not tokens:
        return False
    toks = tokens[1:] if tokens[0] == "sudo" else tokens
    if not toks:
        return False
    base = os.path.basename(toks[0])
    if base.startswith("mkfs"):
        return True
    if base == "dd" and any(t.startswith("of=/dev/") for t in toks):
        return True
    return False


def _is_fork_bomb(command):
    """The classic :(){ :|:& };: fork bomb (and close variants)."""
    return ":(){:|:&};:" in command.replace(" ", "")


_DESTRUCTIVE_TOKEN_CHECKS = (_is_rm_rf_root, _is_mkfs_or_dd_device)


def bash_is_destructive(command):
    """True iff a Bash command is obviously destructive (deny it).

    Best-effort rail against accidental footguns - NOT a defense against an
    adversarial agent; the real bound is repo-confinement on the Write/Edit tools.

    Coarse safety rails: rm -rf of a root/home target, mkfs, dd to a device, a
    fork bomb. An empty or unparseable command (unbalanced quotes) is treated as
    destructive - fail CLOSED. Everything else (installers, auth, cp, python3,
    git, ...) is allowed.
    """
    if not command:
        return True
    if _is_fork_bomb(command):
        return True
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True
    for check in _DESTRUCTIVE_TOKEN_CHECKS:
        if check(tokens):
            return True
    return False
