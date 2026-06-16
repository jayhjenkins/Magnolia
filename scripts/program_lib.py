#!/usr/bin/env python3
"""
program_lib.py - Shared library for PM-OS Cadence program management.

Programs are the canonical store for the Cadence standing-loop subsystem.
They mirror task files exactly: YAML frontmatter + markdown body, IDs
allocated from a locked counter, cross-platform file locking. This module
owns the program file format and its create/read/list operations.

Mirrors scripts/task_lib.py - one implementation, zero drift.
"""

import os
import sys
from datetime import datetime, timezone
from io import StringIO

from ruamel.yaml import YAML

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import platform_lib  # cross-platform file locking (replaces Unix-only fcntl)

# ─── Constants ───────────────────────────────────────────────────────────────

_PM_OS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096  # prevent line wrapping


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _now_iso():
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _datasets_dir(root=None):
    """Return the datasets/ directory for the given root.

    root=None resolves to the engine repo's datasets/ (parent-of-scripts),
    exactly as task_lib resolves its default. A caller-supplied root is treated
    as a PM-OS root, so its datasets/ dir is root/datasets.
    """
    if root is None:
        return os.path.join(_PM_OS_DIR, "datasets")
    return os.path.join(root, "datasets")


def _program_dir(root=None):
    """Return absolute path to the programs directory."""
    return os.path.join(_datasets_dir(root), "programs")


def _counter_path(root=None):
    """Return absolute path to the program ID counter file."""
    return os.path.join(_program_dir(root), "_counter")


def _next_id(root=None):
    """Atomically read, increment, and return next program ID.

    Locks _counter for concurrency safety (cross-platform via platform_lib).
    Returns string like 'PROG-0042'. Creates the programs dir and seeds the
    counter at 1 on first use.
    """
    pdir = _program_dir(root)
    os.makedirs(pdir, exist_ok=True)
    counter = _counter_path(root)
    if not os.path.exists(counter):
        with open(counter, "w", encoding="utf-8") as f:
            f.write("1")

    fd = open(counter, "r+")
    try:
        platform_lib.lock(fd)
        current = int(fd.read().strip())
        program_id = f"PROG-{current:04d}"
        fd.seek(0)
        fd.write(str(current + 1))
        fd.truncate()
        return program_id
    finally:
        platform_lib.unlock(fd)
        fd.close()


def _parse_program_file(filepath):
    """Parse a program file into (frontmatter_dict, body_string).

    Returns (dict, str) where dict is the YAML frontmatter and str is the
    markdown body after the closing ---.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if not content.startswith("---"):
        raise ValueError(f"Program file missing YAML frontmatter: {filepath}")

    # Split on the second ---
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Program file has malformed frontmatter: {filepath}")

    fm_str = parts[1]
    body = parts[2]

    fm = yaml.load(fm_str)
    if fm is None:
        fm = {}

    return fm, body


def _write_program_file(filepath, frontmatter, body):
    """Write a program file with YAML frontmatter + markdown body.

    Includes YAML validation gate: after writing, parses back to verify.
    On failure, reverts and raises.
    """
    # Backup existing content for revert
    backup = None
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            backup = f.read()

    # Serialize frontmatter
    stream = StringIO()
    yaml.dump(frontmatter, stream)
    fm_str = stream.getvalue()

    content = f"---\n{fm_str}---\n{body}"

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    # YAML validation gate: parse back to verify
    try:
        _parse_program_file(filepath)
    except Exception as e:
        # Revert on failure
        if backup is not None:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(backup)
        else:
            os.remove(filepath)
        raise ValueError(f"YAML validation failed after write: {e}")


# ─── Core Operations ─────────────────────────────────────────────────────────

def create_program(type, title, owner_role, intent="",
                    frontmatter_extra=None, root=None):
    """Create a new program file in datasets/programs/.

    Required frontmatter defaults: program_id, type, status (default 'active'),
    title, owner_role, created (ISO now). Any keys in frontmatter_extra are
    passed through (phase, phase_entered, checkpoints, bindings, drift,
    last_cycle, and any metric/series/periods/items/policy/status_line fields).

    Returns (program_id, filepath).
    """
    if not title or len(title) > 200:
        raise ValueError("Title must be non-empty and max 200 characters")
    if not type:
        raise ValueError("type must be non-empty")
    if not owner_role:
        raise ValueError("owner_role must be non-empty")

    frontmatter_extra = frontmatter_extra or {}

    program_id = _next_id(root)
    now = _now_iso()

    frontmatter = {
        "program_id": program_id,
        "type": type,
        "status": "active",
        "title": title,
        "owner_role": owner_role,
        "created": now,
    }
    # Pass-through extras (may override status, etc.)
    for key, value in frontmatter_extra.items():
        frontmatter[key] = value

    body = f"## Intent\n{intent}\n\n## Observations\n\n## Cycles\n"

    pdir = _program_dir(root)
    os.makedirs(pdir, exist_ok=True)
    filepath = os.path.join(pdir, f"{program_id}.md")
    _write_program_file(filepath, frontmatter, body)

    return program_id, filepath


def read_program(program_id, root=None):
    """Read and parse a program file. Returns dict with frontmatter + body."""
    filepath = os.path.join(_program_dir(root), f"{program_id}.md")
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Program {program_id} not found")

    fm, body = _parse_program_file(filepath)
    return {
        "frontmatter": fm,
        "body": body,
        "filepath": filepath,
    }


def list_programs(status=None, root=None):
    """Walk the programs directory, parse frontmatter, return filtered list.

    Returns list of dicts with keys: program_id, frontmatter, body, filepath.
    Filters by status when provided. Sorted by program_id (oldest first).
    """
    results = []
    pdir = _program_dir(root)
    if not os.path.isdir(pdir):
        return results

    for fname in os.listdir(pdir):
        if not fname.startswith("PROG-") or not fname.endswith(".md"):
            continue
        filepath = os.path.join(pdir, fname)
        try:
            fm, body = _parse_program_file(filepath)
        except Exception:
            continue  # skip malformed files

        if status and fm.get("status") != status:
            continue

        results.append({
            "program_id": fm.get("program_id", fname.replace(".md", "")),
            "frontmatter": fm,
            "body": body,
            "filepath": filepath,
        })

    results.sort(key=lambda p: p["program_id"])
    return results
