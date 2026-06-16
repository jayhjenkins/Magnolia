#!/usr/bin/env python3
"""
program_lib.py - Shared library for PM-OS Cadence program management.

Programs are the canonical store for the Cadence standing-loop subsystem.
They mirror task files exactly: YAML frontmatter + markdown body, IDs
allocated from a locked counter, cross-platform file locking. This module
owns the program file format and its create/read/list operations.

Mirrors scripts/task_lib.py - one implementation, zero drift.
"""

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


def render_view(program, registry):
    """Map a program dict (frontmatter + body) into the render contract.

    `program` is the shape returned by read_program (keys: frontmatter, body).
    `registry` is the parsed registry dict (from load_registry). Returns DATA
    ONLY — no styling. The client derives all tone/color from drift/age/status.
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
    rendered = [render_view(p, registry) for p in programs]

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
