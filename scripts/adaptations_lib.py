#!/usr/bin/env python3
"""
adaptations_lib.py - Store + liveness API for the Adapt feature.

One record per adaptation at datasets/adaptations/<id>.md as YAML frontmatter
plus a free-text body. The frontmatter manifest is the single source of truth
for which artifacts (workers, adapters, card-types) an adaptation owns.

Liveness rule (is_live):
- A live owner wins: if any active adaptation owns (surface, ref) with state
  "on", the artifact is live.
- If owned but no active owner is "on", the artifact is not live.
- If no active adaptation owns it, the artifact is live (unowned/legacy
  artifacts are always live - back-compat).

Writes are atomic (tempfile.mkstemp -> write -> os.replace) and use ruamel.yaml
for round-trip frontmatter, mirroring scripts/task_lib.py and
scripts/profile_lib.py house style.
"""

import os
import re
import tempfile
from datetime import datetime, timezone
from io import StringIO

from ruamel.yaml import YAML

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)

STORE_DIR = os.path.join(PM_OS_DIR, "datasets", "adaptations")

STATES = ["pending", "building", "off", "on"]
STATUSES = ["active", "deleted"]

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096  # prevent line wrapping


# --- Helpers -----------------------------------------------------------------

def _now_iso():
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(name):
    """Kebab-case slug from a free-text name (lowercase, ascii word chars)."""
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s or "adaptation"


def _path(adaptation_id):
    return os.path.join(STORE_DIR, f"{adaptation_id}.md")


def _parse(filepath):
    """Parse an adaptation file into (frontmatter_dict, body_string)."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    if not content.startswith("---"):
        raise ValueError(f"Adaptation file missing YAML frontmatter: {filepath}")
    parts = content.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Adaptation file has malformed frontmatter: {filepath}")
    fm = yaml.load(parts[1])
    if fm is None:
        fm = {}
    return fm, parts[2]


def _write(filepath, frontmatter, body):
    """Atomically write frontmatter + body (mkstemp -> write -> os.replace)."""
    stream = StringIO()
    yaml.dump(frontmatter, stream)
    content = f"---\n{stream.getvalue()}---\n{body}"

    dir_ = os.path.dirname(filepath)
    os.makedirs(dir_, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".adaptation-", dir=dir_)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, filepath)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _record(fm, body=""):
    """Project a frontmatter dict into a plain-dict record (ruamel -> plain)."""
    return {
        "id": fm.get("id"),
        "name": fm.get("name"),
        "claude_session_id": fm.get("claude_session_id"),
        "state": fm.get("state"),
        "created": fm.get("created"),
        "status": fm.get("status"),
        "manifest": [dict(e) for e in (fm.get("manifest") or [])],
        "body": body,
    }


# --- Core operations ---------------------------------------------------------

def create(name, session_id):
    """Create a new adaptation record in state `pending`. Returns its id.

    A freshly-created row exists only to KEY a live run by id - it has built
    nothing yet, so it starts `pending` and stays HIDDEN from list_all (and
    therefore from the rail and is_live) until a build lands a manifest and the
    runner promotes it (pending -> off). Slugs the id from name (kebab-case);
    appends -2, -3, ... on collision.
    """
    base = _slug(name)
    adaptation_id = base
    n = 2
    while os.path.exists(_path(adaptation_id)):
        adaptation_id = f"{base}-{n}"
        n += 1

    frontmatter = {
        "id": adaptation_id,
        "name": name,
        "claude_session_id": session_id,
        "state": "pending",
        "created": _now_iso(),
        "status": "active",
        "manifest": [],
    }
    _write(_path(adaptation_id), frontmatter, "\n")
    return adaptation_id


def read(adaptation_id):
    """Read and parse an adaptation file. Returns a plain dict (+ 'body')."""
    filepath = _path(adaptation_id)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Adaptation {adaptation_id} not found")
    fm, body = _parse(filepath)
    return _record(fm, body)


def list_all():
    """Return all active, non-pending adaptation records.

    Excludes tombstoned rows (status == "deleted") AND pending rows (state ==
    "pending"). A pending row exists only to key a live run and has not built
    anything yet, so it must stay hidden from the rail and from is_live (both
    consume list_all). Pending rows have empty manifests, so excluding them
    never changes an is_live result. read(id) STILL returns pending rows - the
    runner reads the pending row by id to promote it.
    """
    results = []
    if not os.path.isdir(STORE_DIR):
        return results
    for fname in os.listdir(STORE_DIR):
        if not fname.endswith(".md"):
            continue
        try:
            fm, body = _parse(os.path.join(STORE_DIR, fname))
        except Exception:
            continue  # skip malformed files
        if fm.get("status") == "deleted":
            continue
        if fm.get("state") == "pending":
            continue  # hidden keying row - not visible until a build lands
        results.append(_record(fm, body))
    results.sort(key=lambda r: r.get("created") or "")
    return results


def update(adaptation_id, changes):
    """Apply frontmatter field changes to an adaptation, preserving the body."""
    filepath = _path(adaptation_id)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Adaptation {adaptation_id} not found")
    fm, body = _parse(filepath)
    for key, value in (changes or {}).items():
        fm[key] = value
    _write(filepath, fm, body)
    return _record(fm, body)


def set_state(adaptation_id, state):
    """Set an adaptation's lifecycle state (pending | building | off | on)."""
    if state not in STATES:
        raise ValueError(f"state must be one of: {STATES}")
    return update(adaptation_id, {"state": state})


def set_name(adaptation_id, name):
    """Rename an adaptation (id is stable; only the display name changes)."""
    return update(adaptation_id, {"name": name})


def add_artifact(adaptation_id, surface, ref, commit):
    """Upsert a (surface, ref, commit) entry in an adaptation's manifest.

    Keyed on (surface, ref): if an entry with that surface+ref already exists,
    replace its commit in place (order is preserved); otherwise append. This
    keeps the resume/re-commit path idempotent rather than accumulating
    duplicate manifest rows.
    """
    filepath = _path(adaptation_id)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Adaptation {adaptation_id} not found")
    fm, body = _parse(filepath)
    manifest = fm.get("manifest")
    if not isinstance(manifest, list):
        manifest = []
        fm["manifest"] = manifest
    for entry in manifest:
        if entry.get("surface") == surface and entry.get("ref") == ref:
            entry["commit"] = commit
            break
    else:
        manifest.append({"surface": surface, "ref": ref, "commit": commit})
    _write(filepath, fm, body)
    return _record(fm, body)


def tombstone(adaptation_id):
    """Mark an adaptation deleted (append-only; never removes the file).

    Invariant #6: never delete generated artifacts.
    """
    return update(adaptation_id, {"status": "deleted"})


# --- Liveness ----------------------------------------------------------------

def adaptation_live(adaptation_id):
    """True if the adaptation exists, is active, and is in state 'on'."""
    try:
        rec = read(adaptation_id)
    except FileNotFoundError:
        return False
    if rec.get("status") == "deleted":
        return False
    return rec.get("state") == "on"


def is_live(surface, ref):
    """Liveness of an artifact (surface, ref) per the manifest source of truth.

    A live owner wins: if ANY active adaptation owns (surface, ref) with
    state == "on", the artifact is live (return True) regardless of how many
    other active owners are off/building. If (surface, ref) is owned but no
    active owner is on, return False. If no active adaptation owns it, return
    True (unowned/legacy artifacts are always live - back-compat).
    """
    owned = False
    for rec in list_all():
        for entry in rec.get("manifest") or []:
            if entry.get("surface") == surface and entry.get("ref") == ref:
                owned = True
                if rec.get("state") == "on":
                    return True  # a live owner wins
    return not owned
