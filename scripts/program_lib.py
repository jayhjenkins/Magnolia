#!/usr/bin/env python3
"""
program_lib.py - Shared library for PM-OS Cadence program management.

Programs are the canonical store for the Cadence standing-loop subsystem.
They mirror task files exactly: YAML frontmatter + markdown body, IDs
allocated from a locked counter, cross-platform file locking. This module
owns the program file format and its create/read/list operations.

Mirrors scripts/task_lib.py - one implementation, zero drift.
"""

import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
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

    # Open create+read+write so we can seed under the lock without a TOCTOU
    # window: two concurrent first-callers would otherwise both seed.
    fd = open(counter, "a+", encoding="utf-8")
    try:
        platform_lib.lock(fd)
        fd.seek(0)
        raw = fd.read().strip()
        # Counter holds the NEXT id; seed at 1 so the first program is PROG-0001
        # (task_lib's conftest seeds "0" instead; here there is no conftest).
        current = int(raw) if raw else 1
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


# ─── Render layer (file -> render contract) ────────────────────────────────────
#
# render_view maps a canonical program file into the terse render contract the
# designer's prototype JS (rowVM / buildSeries) expects. It returns DATA ONLY —
# no inline CSS, colors, or style dicts. The JS layer owns every tone/color
# decision (from drift / age / status); render_view derives only the raw values
# (current phase index, metric delta, chart geometry, activity feed, etc.).
#
# The registry lives in the ENGINE (cadence/programtypes/registry.json), not
# under datasets, so load_registry resolves to the repo root regardless of the
# programs `root`. build_cadence_payload reads programs from `root` but the
# registry from the engine.

# buildSeries geometry constants — ported verbatim from the prototype.
_SERIES_W = 300
_SERIES_H = 66
_SERIES_PAD = 5
_SERIES_TOL = 8

# Matches an Observations entry header: "### YYYY-MM-DD — sentinel:NAME [kind]".
# The sentinel name and the [kind] are both optional, kept tolerant. The
# separator accepts an ASCII hyphen AND en/em dash, since the spec-conformant
# brief format authors the header with an em-dash (—) — reading authored
# content tolerantly, not emitting it (so this is outside invariant #8).
_OBS_HEADER_RE = re.compile(
    r"^###\s+(?P<date>\d{4}-\d{2}-\d{2})\s*"
    r"(?:[-–—]\s*(?:sentinel:(?P<sentinel>\S+))?\s*(?:\[(?P<kind>[^\]]+)\])?)?\s*$"
)

# The Observations section heading (the anchor append_observation inserts under).
_OBS_HEADING = "## Observations"
# Line-anchored match for a real `## Observations` heading (not a heading-shaped
# substring inside prose) -- used for the presence check so the writer never
# splices an entry into the middle of a paragraph that merely mentions the text.
_OBS_HEADING_RE = re.compile(r"^## Observations\s*$", re.MULTILINE)

# Closed observation-kind enum. Sentinels (later tasks) may only emit one of
# these; append_observation rejects anything outside the set. Kept ASCII-safe.
OBSERVATION_KINDS = frozenset({
    "status-signal",
    "date-change",
    "completion",
    "commitment",
    "risk",
    "metric",
    "capture",
    "blocker",
})


def tracker_anchor(fm):
    """Return the project-management tracker ref for a program, or None.

    The shared binding-resolution helper (the I1 seam): seed programs carry the
    tracker ref under `bindings[]` as `{role: truth, kind: project_management,
    anchor: "EPIC-204"}`. Reads the first such binding's `anchor`; falls back to
    the legacy `links.tracker_epic`; else None. Defensive against missing or
    malformed shapes (bindings not a list, entries not dicts, links not a dict) -
    never raises. Both sentinel_runner (tracker-truth) and the reconciler (fact
    door) resolve the tracker through here so they match the real seeds.
    """
    fm = fm or {}
    if not isinstance(fm, dict):
        return None
    bindings = fm.get("bindings")
    if isinstance(bindings, list):
        for b in bindings:
            if not isinstance(b, dict):
                continue
            if b.get("role") == "truth" and b.get("kind") == "project_management":
                anchor = b.get("anchor")
                if anchor:
                    return str(anchor)
    links = fm.get("links")
    if isinstance(links, dict):
        epic = links.get("tracker_epic")
        if epic:
            return str(epic)
    return None


def load_registry(root=None):
    """Read and parse cadence/programtypes/registry.json from the engine repo.

    The registry is an engine artifact (NOT under datasets), so this always
    resolves to the repo root's cadence/programtypes/registry.json, ignoring a
    caller-supplied `root` (which addresses a PM-OS datasets root). The `root`
    arg is accepted for call-site symmetry but does not relocate the registry.
    Returns the parsed dict.
    """
    path = os.path.join(_PM_OS_DIR, "cadence", "programtypes", "registry.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_intent(body):
    """Return the text under the body's `## Intent` section (stripped), or ''."""
    if not body:
        return ""
    lines = body.splitlines()
    out = []
    in_intent = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_intent:
                break  # next section ends Intent
            in_intent = stripped[3:].strip().lower() == "intent"
            continue
        if in_intent:
            out.append(line)
    return "\n".join(out).strip()


def _parse_observations(body):
    """Parse the body's `## Observations` section into activity entries.

    Each observation looks like:

        ### YYYY-MM-DD - sentinel:NAME [kind]
        claim: <one-line claim text>
        source: <optional>

    Returns a list of {date, text, tag} dicts, most-recent-first (by reverse
    document order — newest entries are appended, so we reverse). Degrades to an
    empty list when the section is absent or in a simpler/unparseable shape.
    """
    if not body:
        return []
    lines = body.splitlines()
    # Isolate the Observations section.
    section = []
    in_section = False
    for line in lines:
        if line.strip().startswith("## "):
            if in_section:
                break
            in_section = line.strip()[3:].strip().lower() == "observations"
            continue
        if in_section:
            section.append(line)
    if not section:
        return []

    entries = []
    current = None
    for line in section:
        m = _OBS_HEADER_RE.match(line.strip())
        if m:
            if current is not None:
                entries.append(current)
            tag = m.group("sentinel") or m.group("kind") or ""
            current = {"date": m.group("date"), "text": "", "tag": tag}
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.lower().startswith("claim:") and not current["text"]:
            current["text"] = stripped[len("claim:"):].strip()
    if current is not None:
        entries.append(current)

    # Drop entries that never found a claim line.
    entries = [e for e in entries if e["text"]]
    entries.reverse()  # most-recent-first
    return entries


def _split_at_next_section(text):
    """Split `text` at the next top-level `## ` heading.

    Returns (section, rest): `section` is the content up to the next top-level
    heading line; `rest` is that heading and everything after it (or "" when no
    following section exists). `### ` entry sub-headers are NOT top-level, so
    they stay in `section`.

    Replicated from reconcile._split_at_next_section (NOT imported): program_lib
    is the lower layer — reconcile imports program_lib, never the reverse. The
    two copies are deliberately identical; keep them in sync.
    """
    idx = text.find("\n## ")
    if idx == -1:
        return text, ""
    return text[:idx], text[idx + 1:]


def _obs_hash(kind, source, claim):
    """Content hash over (kind, source, claim) for observation dedupe.

    Sources/claims are stripped before hashing so trivial whitespace differences
    do not defeat the dedupe. Sentinel name, date, and confidence are NOT part of
    the identity: the same factual claim from the same source is one observation
    no matter who saw it or when.
    """
    payload = "\x00".join((kind, source.strip(), claim.strip()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _existing_obs_hashes(body):
    """Return the set of content hashes for observations already on `body`.

    Parses the `## Observations` section header-by-header, reading each entry's
    `kind` (from the header), `source:` line, and `claim:` line, then hashing
    them the same way _obs_hash does. Tolerant: an entry missing a source/claim
    simply contributes no hash. Used only for dedupe, so a miss errs toward
    appending (never toward silently dropping a real observation).
    """
    if not body:
        return set()
    lines = body.splitlines()
    section = []
    in_section = False
    for line in lines:
        if line.strip().startswith("## "):
            if in_section:
                break
            in_section = line.strip()[3:].strip().lower() == "observations"
            continue
        if in_section:
            section.append(line)

    hashes = set()
    kind = source = claim = None

    def _flush():
        if kind and source is not None and claim is not None:
            hashes.add(_obs_hash(kind, source, claim))

    for line in section:
        m = _OBS_HEADER_RE.match(line.strip())
        if m:
            _flush()
            kind = (m.group("kind") or "").strip() or None
            source = claim = None
            continue
        stripped = line.strip()
        low = stripped.lower()
        if low.startswith("source:") and source is None:
            source = stripped[len("source:"):].strip()
        elif low.startswith("claim:") and claim is None:
            claim = stripped[len("claim:"):].strip()
    _flush()
    return hashes


def append_observation(program_id, *, kind, sentinel, source, claim,
                       date=None, confidence=None, root=None):
    """Append a structured observation under a program's `## Observations`.

    The deterministic, validated write path for the Cadence interpretation
    engine: sentinels (LLM agents) return observation records and this function
    — never the LLM — appends them to the program file. Append-only (invariant
    #6): prior observations are never rewritten or dropped.

    Args (keyword-only after program_id):
        kind: one of OBSERVATION_KINDS (else ValueError).
        sentinel: name of the sentinel that produced the observation.
        source: non-empty citation of where the claim came from (else ValueError).
        claim: non-empty one-line factual claim (else ValueError).
        date: ISO date string for the entry header; defaults to today (UTC).
        confidence: optional numeric confidence; emitted only when provided.
        root: PM-OS datasets root (defaults to the engine repo's datasets/).

    Dedupe: identical (kind, stripped source, stripped claim) already present on
    the program is a no-op — returns False and leaves the file untouched. On a
    successful append returns True.

    Entry format (ASCII hyphen in the header, invariant #8 — never an em-dash):

        ### <date> - sentinel:<sentinel> [<kind>]
        source: <source>
        claim: <claim>
        confidence: <n>      (only when confidence is provided)
    """
    if kind not in OBSERVATION_KINDS:
        raise ValueError(
            f"kind must be one of {sorted(OBSERVATION_KINDS)}, got: {kind!r}")
    if not source or not source.strip():
        raise ValueError("source must be non-empty")
    if not claim or not claim.strip():
        raise ValueError("claim must be non-empty")

    prog = read_program(program_id, root=root)
    body = prog["body"] or ""
    filepath = prog["filepath"]

    # Content-hash dedupe over (kind, source, claim).
    if _obs_hash(kind, source, claim) in _existing_obs_hashes(body):
        return False

    entry_date = date or _now_iso()[:10]
    entry_lines = [
        f"### {entry_date} - sentinel:{sentinel} [{kind}]",
        f"source: {source.strip()}",
        f"claim: {claim.strip()}",
    ]
    if confidence is not None:
        # Uniform 2-decimal rendering so the ledger stays tidy regardless of the
        # caller's float precision; non-numeric confidence falls back to str().
        try:
            entry_lines.append(f"confidence: {float(confidence):.2f}")
        except (TypeError, ValueError):
            entry_lines.append(f"confidence: {confidence}")
    entry = "\n".join(entry_lines) + "\n"

    # Both this insert and _existing_obs_hashes assume a single `## Observations`
    # section (the canonical program format); the heading-anchored splice below
    # uses the last heading and dedupe reads the first -- aligned only when there
    # is one, which the format guarantees.
    if not _OBS_HEADING_RE.search(body):
        # No Observations section: create one at the end of the body.
        base = body.rstrip("\n")
        new_body = (f"{base}\n\n{_OBS_HEADING}\n\n{entry}"
                    if base else f"{_OBS_HEADING}\n\n{entry}")
    else:
        # Anchor on the LAST Observations heading. The head (everything up to and
        # including the heading) is preserved verbatim; the trailing text is split
        # at the next top-level `## ` heading so a following `## Cycles` (and its
        # entries) is preserved verbatim. The new entry lands at the end of the
        # Observations content, before that following section. Mirrors
        # reconcile._append_cycle_entry.
        head, sep, tail = body.rpartition(_OBS_HEADING)
        section, rest = _split_at_next_section(tail)
        section = section.rstrip("\n")
        if rest:
            new_body = f"{head}{sep}{section}\n\n{entry}\n{rest}"
        else:
            new_body = f"{head}{sep}{section}\n\n{entry}"

    _write_program_file(filepath, prog["frontmatter"], new_body)
    return True


# ─── Phase advancement core (shared by the fact door + the proposal applier) ──
#
# _next_phase_id + _advance_phase_fm are the ONE place the engine advances a
# pipeline phase. Both the reconciler's FACT door (reconcile._maybe_advance_phase)
# and the human-accept PROPOSAL applier (apply_mutation, below) advance through
# here, so an auto-advance and a human-accepted advance touch the frontmatter
# IDENTICALLY: the same next-phase lookup and the same dict-vs-scalar
# phase_entered stamping. program_lib is the LOWER layer (reconcile imports
# program_lib, never the reverse), so this shared logic lives here and reconcile
# calls in.


def _next_phase_id(type_entry, phase):
    """Return the id of the phase AFTER `phase` in the type's order, or None.

    None when `phase` is unknown, is the last phase, or has no successor. The
    canonical next-phase lookup for BOTH advancement doors (DRY).
    """
    phases = (type_entry or {}).get("phases") or []
    ids = [p.get("id") for p in phases if isinstance(p, dict)]
    try:
        i = ids.index(phase)
    except ValueError:
        return None
    if i + 1 >= len(ids):
        return None
    return ids[i + 1]


def _advance_phase_fm(fm, next_phase, today):
    """Mutate `fm` in place to enter `next_phase` as of `today` (ISO date string).

    Sets `fm["phase"]` and stamps `phase_entered` for the new phase, PRESERVING
    the existing dict-vs-scalar form: a dict {phase_id: date} gets the new phase
    keyed in (prior entries kept); a scalar (the brief's form = the date the
    CURRENT phase was entered) is overwritten with the new entry date; a missing
    value is initialized to the scalar form. The single shared stamp used by the
    fact door and the proposal applier so they never drift.
    """
    fm["phase"] = next_phase
    entered = fm.get("phase_entered")
    if isinstance(entered, dict):
        entered[next_phase] = today
    else:
        fm["phase_entered"] = today  # scalar form = the current phase's entry date


# ─── Proposal applier (the closed mutation set behind a human accept) ─────────
#
# apply_mutation is the human-side counterpart to the reconciler's fact door:
# when a human ACCEPTS a Cadence propose-update card, the program mutation rides
# here. Tier-1: a LOCAL program-file write only -- no external write, no git
# commit, no second shipper. Closed set: advance-phase + adjust-checkpoint;
# anything else is refused (ValueError) with NO mutation. Append-only (invariant
# #6): an advance appends a completion observation and never deletes. ASCII-safe
# runtime strings (invariant #8).

_MUTATION_OPS = frozenset({"advance-phase", "adjust-checkpoint"})


def apply_mutation(program_id, mutation, root=None):
    """Apply a closed-set program mutation. Returns a small result dict.

    mutation is a dict carrying an `op`:
      - {"op": "advance-phase", "to": <phase>, "checkpoint": <cp>?, "from": <phase>?}
        Sets `phase` to `to` (refusing to advance past a terminal phase), stamps
        `phase_entered` for the new phase to today (dict-vs-scalar form preserved),
        and appends a `completion` fact observation (sentinel=reconciler;
        source=`checkpoint:<cp>` when a checkpoint is carried, else `proposal`).
      - {"op": "adjust-checkpoint", "id": <cp_id>, "due": <iso>?, "status": "met"?}
        Changes that checkpoint's `due` and/or `status`. Setting the CURRENT
        phase's exit_checkpoint to met cascades to advance the phase via the same
        advance path.

    An out-of-set or missing `op` raises ValueError with NO mutation. An
    adjust-checkpoint naming an unknown checkpoint id is refused (no mutation,
    returns a refused status). Append-only, ASCII-safe.

    Returns one of:
      {"applied": "advance-phase", "program_id", "from", "to", "checkpoint"}
      {"applied": "adjust-checkpoint", "program_id", "id", "advanced": {...}|None}
      {"applied": None, "status": "refused", "reason": <ascii>, "program_id"}
    """
    if not isinstance(mutation, dict):
        raise ValueError("mutation must be a dict carrying an 'op'")
    op = mutation.get("op")
    if op not in _MUTATION_OPS:
        raise ValueError(
            f"op must be one of {sorted(_MUTATION_OPS)}, got: {op!r}")

    registry = load_registry()
    prog = read_program(program_id, root=root)
    fm = prog["frontmatter"]
    type_id = fm.get("type")
    type_entry = next(
        (t for t in registry.get("types", []) if t.get("id") == type_id), {}
    )

    if op == "advance-phase":
        return _apply_advance_phase(program_id, mutation, fm, type_entry,
                                    prog["filepath"], prog["body"], root)
    return _apply_adjust_checkpoint(program_id, mutation, fm, type_entry,
                                    prog["filepath"], prog["body"], root)


def _terminal_phase(type_entry, phase):
    """True when `phase` is declared terminal in the type (or has no successor)."""
    phases = (type_entry or {}).get("phases") or []
    phase_def = next(
        (p for p in phases if isinstance(p, dict) and p.get("id") == phase), {}
    )
    return bool(phase_def.get("terminal"))


def _apply_advance_phase(program_id, mutation, fm, type_entry, filepath, body, root):
    """advance-phase: stamp the new phase + append a completion fact observation.

    Idempotent + non-skipping. When the mutation names a target `to`:
      - if the program is ALREADY at `to`, this is a no-op success (a retried
        accept after a partial failure must NOT advance a second time);
      - it advances only when the current phase is exactly the predecessor of `to`
        (so a stale proposal -- the program moved on since it was made -- is
        refused, never advanced to an unintended phase).
    With no `to`, it advances to the registry's next phase. Refuses (no mutation)
    when the current phase is terminal or has no successor. The completion
    observation cites the carried checkpoint or, absent one, `proposal`. Writes the
    frontmatter change FIRST, then appends the observation (append_observation
    re-reads + rewrites the file, so it must run after the frontmatter write,
    mirroring reconcile_program's two-write order).
    """
    current = fm.get("phase")
    to = mutation.get("to")
    # Idempotent: already at the proposed target -> the mutation was already
    # applied (e.g. a retried accept). No second advance.
    if to and current == to:
        return {"applied": None, "status": "noop",
                "reason": f"already at {to}", "program_id": program_id,
                "from": current, "to": to}
    if _terminal_phase(type_entry, current):
        return {"applied": None, "status": "refused",
                "reason": "phase is terminal", "program_id": program_id}
    next_phase = _next_phase_id(type_entry, current)
    if not next_phase:
        return {"applied": None, "status": "refused",
                "reason": "no successor phase", "program_id": program_id}
    # A `to` must be the immediate successor of the current phase; if it is not,
    # the proposal is stale (the program advanced since it was made) -> refuse
    # rather than advance to an unintended phase.
    if to and to != next_phase:
        return {"applied": None, "status": "refused",
                "reason": f"proposal target {to} is not the phase after {current}",
                "program_id": program_id}
    target = to or next_phase

    today = _now_iso()[:10]
    _advance_phase_fm(fm, target, today)
    _write_program_file(filepath, fm, body)

    checkpoint = mutation.get("checkpoint")
    source = f"checkpoint:{checkpoint}" if checkpoint else "proposal"
    append_observation(
        program_id, kind="completion", sentinel="reconciler", source=source,
        claim=f"Phase advanced {current} -> {target}.", date=today, root=root)

    return {"applied": "advance-phase", "program_id": program_id,
            "from": current, "to": target, "checkpoint": checkpoint}


def _apply_adjust_checkpoint(program_id, mutation, fm, type_entry, filepath, body, root):
    """adjust-checkpoint: change a checkpoint's due and/or status.

    Setting the CURRENT phase's exit_checkpoint to met cascades to advance the
    phase through the same advance path (so an accepted "mark met" and an
    accepted "advance" land identically). An unknown checkpoint id is refused.
    """
    cp_id = mutation.get("id")
    checkpoints = fm.get("checkpoints") or []
    cp = next(
        (c for c in checkpoints if isinstance(c, dict) and c.get("id") == cp_id),
        None,
    )
    if cp is None:
        return {"applied": None, "status": "refused",
                "reason": f"no checkpoint {cp_id!r}", "program_id": program_id}

    # Refuse a no-op adjust (neither a new due nor a met flag) rather than report a
    # false success and needlessly rewrite the file.
    has_due = mutation.get("due") is not None
    if not has_due and mutation.get("status") != "met":
        return {"applied": None, "status": "refused",
                "reason": "adjust-checkpoint needs a due or status:met",
                "program_id": program_id}

    if "due" in mutation and mutation["due"] is not None:
        cp["due"] = mutation["due"]
    set_met = mutation.get("status") == "met"
    if set_met:
        cp["status"] = "met"

    # Cascade: met on the CURRENT phase's exit checkpoint advances the phase.
    advanced = None
    current = fm.get("phase")
    phases = (type_entry or {}).get("phases") or []
    phase_def = next(
        (p for p in phases if isinstance(p, dict) and p.get("id") == current), {}
    )
    cascade = (set_met and phase_def.get("exit_checkpoint") == cp_id
               and not _terminal_phase(type_entry, current))
    next_phase = _next_phase_id(type_entry, current) if cascade else None

    today = _now_iso()[:10]
    if next_phase:
        _advance_phase_fm(fm, next_phase, today)
        advanced = {"from": current, "to": next_phase, "checkpoint": cp_id}

    _write_program_file(filepath, fm, body)
    if advanced:
        append_observation(
            program_id, kind="completion", sentinel="reconciler",
            source=f"checkpoint:{cp_id}",
            claim=f"Phase advanced {current} -> {next_phase}.",
            date=today, root=root)

    return {"applied": "adjust-checkpoint", "program_id": program_id,
            "id": cp_id, "advanced": advanced}


def _build_series(series):
    """Port of the prototype buildSeries(p) math — geometry only, no stroke.

    series is {pred:[...], act:[...]}. Returns {predPts, actPts, band, lastX,
    lastY} as strings. Color/tone is the client's job (from drift), so `stroke`
    is intentionally omitted.
    """
    pred = list(series.get("pred", []))
    act = list(series.get("act", []))
    n = len(pred)
    if n < 2:
        return {"predPts": "", "actPts": "", "band": "", "lastX": "", "lastY": ""}

    W, H, PAD, tol = _SERIES_W, _SERIES_H, _SERIES_PAD, _SERIES_TOL
    max_v = max(pred + act) * 1.18
    if max_v == 0:
        max_v = 1  # avoid divide-by-zero on an all-zero series

    def x(i):
        return PAD + (i / (n - 1)) * (W - 2 * PAD)

    def y(v):
        return H - PAD - (v / max_v) * (H - 2 * PAD)

    pred_pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(pred))
    act_pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(act))
    top = [f"{x(i):.1f},{y(v + tol):.1f}" for i, v in enumerate(pred)]
    bot = [f"{x(i):.1f},{y(v - tol):.1f}" for i, v in enumerate(pred)][::-1]
    li = n - 1
    # `act` may be shorter than `pred` (a target mid-flight has fewer actuals
    # than predicted points). Anchor the last actual point to the final
    # available actual; fall back to the last predicted point when act is empty,
    # so a single mismatched series never raises and 500s the whole endpoint.
    if act:
        last_act_x = x(min(li, len(act) - 1))
        last_act_y = y(act[min(li, len(act) - 1)])
    else:
        last_act_x = x(li)
        last_act_y = y(pred[li])
    return {
        "predPts": pred_pts,
        "actPts": act_pts,
        "band": " ".join(top + bot),
        "lastX": f"{last_act_x:.1f}",
        "lastY": f"{last_act_y:.1f}",
    }


def _jsonable(obj):
    """Recursively coerce date/datetime values to ISO-8601 strings.

    ruamel parses UNQUOTED ISO dates in program frontmatter (checkpoint `due`,
    `phase_entered`, binding `last`) into datetime.date objects, which the
    stdlib json encoder cannot serialize without a `default=` hook. Walk dicts
    and lists, converting any date/datetime to `.isoformat()` (datetime is a
    subclass of date, so both are caught and isoformat works on both), leaving
    everything else untouched. This makes the output strictly JSON-clean and
    encoder-independent. Note: the isoformat string is byte-identical to what
    json.dumps(..., default=str) already produces on the wire, so wire values
    are unchanged — this just makes the cleanliness explicit.
    """
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, date):  # covers datetime (subclass of date)
        return obj.isoformat()
    return obj


def render_view(program, registry, needs_you=0):
    """Map a program dict (frontmatter + body) into the render contract.

    `program` is the shape returned by read_program (keys: frontmatter, body).
    `registry` is the parsed registry dict (from load_registry). `needs_you` is
    the count of open Now (human-queue) cards linked to this program; the caller
    (build_cadence_payload) supplies it, defaulting to 0 for unit-test/call-site
    simplicity. Returns DATA ONLY — no styling. The client derives all tone/color
    from drift/age/status.
    """
    fm = program.get("frontmatter", {}) or {}
    body = program.get("body", "") or ""

    type_id = fm.get("type")
    type_entry = next(
        (t for t in registry.get("types", []) if t.get("id") == type_id), {}
    )
    state_model = type_entry.get("state_model")
    # An unknown type yields an empty type_entry, so family is None — such a
    # program matches no registry family and is silently dropped from the
    # payload by build_cadence_payload (intentional: unregistered types don't render).
    family = type_entry.get("family")
    type_label = type_entry.get("label", type_id)

    vm = {
        "id": fm.get("program_id"),
        "name": fm.get("title"),
        "model": state_model,
        "drift": fm.get("drift", "holding"),
        "intent": _parse_intent(body),
        "family": family,
        "type_label": type_label,
        "activity": _parse_observations(body),
        # Canonical checkpoint keys are {id, label, due, instrument, status}.
        "checkpoints": [
            {
                "label": c.get("label"),
                "due": c.get("due"),
                "instrument": c.get("instrument"),
                "status": c.get("status"),
            }
            for c in (fm.get("checkpoints") or [])
        ],
        "bindings": [
            {
                "role": b.get("role"),
                "anchor": b.get("anchor"),
                "health": b.get("health", "ok"),
                "last": b.get("last"),
            }
            for b in (fm.get("bindings") or [])
        ],
        "cadence": fm.get("cadence", type_entry.get("cadence")),
        "last_cycle": fm.get("last_cycle"),
        "last_run": fm.get("last_run"),
        # Count of open Now cards linked to this program (supplied by the caller).
        "needs_you": needs_you,
    }

    if state_model == "pipeline":
        phases_def = type_entry.get("phases", []) or []
        current_phase = fm.get("phase")
        # phase_entered tolerates two shapes: a dict {phase_id: date} (the
        # richer seed form) and a scalar date string (the brief's form — the
        # date the CURRENT phase was entered). Normalize the scalar into a
        # dict keyed by the current phase id; missing/None -> no entered dates.
        raw_entered = fm.get("phase_entered")
        if isinstance(raw_entered, dict):
            entered = raw_entered
        elif raw_entered:
            entered = {current_phase: raw_entered}
        else:
            entered = {}
        current = 0
        for i, ph in enumerate(phases_def):
            if ph.get("id") == current_phase:
                current = i
                break
        vm["current"] = current
        vm["phases"] = [
            {
                "label": ph.get("label"),
                "window": ph.get("max_age_days"),
                "entered": entered.get(ph.get("id")),
            }
            for ph in phases_def
        ]
    elif state_model == "target":
        metric = fm.get("metric") or {}
        actual = metric.get("actual")
        target = metric.get("target")
        unit = metric.get("unit", "")
        vm["metric"] = {"actual": actual, "target": target, "unit": unit}
        if actual is not None and target is not None:
            delta = actual - target
            sign = "+" if delta >= 0 else "-"  # ASCII hyphen, not unicode minus
            vm["delta_str"] = f"{sign}{abs(delta)}pt"
        else:
            vm["delta_str"] = ""
        vm["series"] = _build_series(fm.get("series") or {})
    elif state_model == "cycle":
        vm["status_line"] = fm.get("status_line")
        vm["periods"] = [
            {"w": p.get("w"), "s": p.get("s")} for p in (fm.get("periods") or [])
        ]
    elif state_model == "register":
        vm["status_line"] = fm.get("status_line")
        vm["items"] = [
            {"name": it.get("name"), "owner": it.get("owner"), "age": it.get("age")}
            for it in (fm.get("items") or [])
        ]
        vm["policy"] = fm.get("policy")

    # Coerce any date/datetime values (from unquoted YAML dates) to ISO strings
    # so the contract is strictly JSON-clean for any encoder (see _jsonable).
    return _jsonable(vm)


def build_cadence_payload(root=None):
    """Assemble the Cadence tab payload: active programs grouped by family.

    Reads active programs from `root` (the datasets root) and the registry from
    the engine. Groups rendered programs by their type's family, in the registry
    `families` order, dropping families with no active programs. Returns
    {"families": [{"id", "label", "programs": [render_view ...]}, ...]}.
    """
    registry = load_registry()
    programs = list_programs(status="active", root=root)

    # Count open Now (human-queue) cards per program: a single pass over open
    # human tasks, tallying any tag shaped like a program id (PROG-XXXX). The
    # emitter tags an escalate card [program_id, "cadence"], so each card counts
    # once toward its program. task_lib is imported lazily (like cron_lib) to
    # avoid a hard coupling at module load, and the whole listing is wrapped in
    # try/except so a task-system failure NEVER breaks the payload (counts -> 0).
    counts = {}
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import task_lib
        # {4,} not {4}: _next_id zero-pads to a MINIMUM of 4 digits, so program
        # ids past PROG-9999 are longer and must still count toward needs_you.
        prog_id_re = re.compile(r"^PROG-\d{4,}$")
        for t in task_lib.list_tasks(queue="human", status="open"):
            seen = set()
            for tag in (t.get("tags") or []):
                if prog_id_re.match(str(tag)) and tag not in seen:
                    counts[tag] = counts.get(tag, 0) + 1
                    seen.add(tag)
    except Exception:
        counts = {}  # task system unavailable — every needs_you defaults to 0

    rendered = []
    for p in programs:
        program_id = (p.get("frontmatter") or {}).get("program_id") or p.get("program_id")
        rendered.append(render_view(p, registry, needs_you=counts.get(program_id, 0)))

    families = []
    for fam in sorted(
        registry.get("families", []), key=lambda f: f.get("order", 0)
    ):
        fam_id = fam.get("id")
        fam_programs = [r for r in rendered if r.get("family") == fam_id]
        if not fam_programs:
            continue  # drop empty families
        families.append(
            {"id": fam_id, "label": fam.get("label"), "programs": fam_programs}
        )

    return {"families": families}
