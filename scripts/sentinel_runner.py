#!/usr/bin/env python3
"""sentinel_runner.py - the cron -> claude -p dispatch harness for Cadence sentinels.

A sibling to adapt_runner / chat_runner, but for the read-only sentinel primitive.
Cron fires it with a sentinel name; this module:

  1. loads the sentinel definition (sentinel_lib.load_sentinel),
  2. resolves scope: the active programs and each one's `## Intent`,
  3. builds a prompt = the sentinel's prompt body + the active-program context
     (ids + intents) + an in-window source digest,
  4. dispatches the LLM through the small _dispatch seam (mirrors judge.run_claude),
  5. parses the returned JSON observation records DEFENSIVELY, and
  6. appends each valid, attributed record via program_lib.append_observation -
     the deterministic, validated writer. THE LLM NEVER WRITES FILES.

The LLM's only job is to return records. The runner decides what to record: a
record is DROPPED (never force-attributed) if its program_id is empty or is not
among the active programs; append_observation's own validation + dedupe is the
SECOND fence (a ValueError or a False return is counted, never crashes the run).

A malformed dispatch (bad JSON, claude error) yields zero observations, is logged
once, and never raises - a bad sentinel run never corrupts a program.

The tracker-truth path is MECHANICAL - it does NOT use the LLM. It reads tracker
facts through the project_management adapter's free read op (adapters.fetch_status,
not Tier-2 gated - a read of the team's system of record is free) and maps them
deterministically to observations: a done/closed status -> completion, otherwise
-> status-signal. When that adapter is NOT configured (the current state on this
box) the run is a clean no-op: it logs once and returns the empty summary without
dispatching. movement-watch (transcripts) is unaffected - it still dispatches the
LLM, which only RETURNS records the runner records.

Identity is read ONLY via profile_lib (invariant #1). All runtime strings are
ASCII (invariant #8). The claude subprocess is built with platform_lib so the
binary/PATH resolve cross-platform - never a hardcoded "claude" or a shell.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PM_OS_DIR = os.path.dirname(SCRIPT_DIR)

sys.path.insert(0, SCRIPT_DIR)
import adapters  # noqa: E402
import platform_lib  # noqa: E402
import profile_lib  # noqa: E402
import program_lib  # noqa: E402
import sentinel_lib  # noqa: E402

# Sentinels run on the standard dispatch tier (they read sources and judge
# attribution - not trivial classification, not heavy coding). Resolved through
# profile_lib so the cost posture and overrides apply like the rest of dispatch.
SENTINEL_MODEL_TIER = "standard"

# How long a single sentinel dispatch may run before we give up (seconds).
CLAUDE_TIMEOUT = 600

# The source kind that is grounded in the project-management adapter, and the
# adapter family it resolves to. A sentinel whose sources are ALL this kind runs
# the mechanical (adapter-read) path, NOT the LLM dispatch path; it degrades to a
# clean no-op when the adapter is unconfigured (tracker-truth today).
_ADAPTER_SOURCE_KIND = "project_management"
_ADAPTER_FAMILY = "project_management"

# The read-only sheet source kind (the EOS sheet). A sentinel whose sources are
# ALL this kind reads the sheet LIVE at dispatch via the M365 MCP. When the
# operator has configured no sheet locator the sentinel is BLIND (it cannot read)
# rather than quiet-but-live - recorded success=False so the silent-archive door
# never treats an EOS program as dormant off it.
_SHEET_SOURCE_KIND = "eos_sheet"

# Tracker statuses (lower-cased) that mechanically support a `completion`
# observation. Anything else maps to `status-signal`. No interpretation.
_DONE_STATUSES = {"done", "closed", "complete", "completed", "resolved", "shipped"}

# The intake sentinel: it returns ROUTING records (observe/capture/candidate/
# ignore) instead of program-attributed observations, so run_sentinel routes it
# through the apply-routes branch. Matched by name (the simplest detector that
# leaves movement-watch/tracker-truth untouched).
_INTAKE_SENTINEL = "program-intake"
# The program type whose single active program is the candidate nursery. The
# intake sentinel's `candidate` route upserts into it.
_INTAKE_PROGRAM_TYPE = "program-intake"
# The closed routing verb set the intake sentinel may emit. Anything else is a
# bad record and is dropped (counted, never raised).
_INTAKE_ROUTES = {"observe", "capture", "candidate", "ignore"}


def log(msg):
    """ASCII-safe stderr log line (invariant #8)."""
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[sentinel {ts}] {msg}", file=sys.stderr, flush=True)


# --- The dispatch seam -------------------------------------------------------

def _dispatch(prompt, tier=None, timeout=None):
    """Call claude headless and return the assistant text, or None.

    Mirrors judge.run_claude: resolve the binary via platform_lib (cross-platform,
    invariant #8), pass `-p`, `--output-format json`, and the headless env. The
    `--output-format json` envelope wraps the assistant text in {"result": ...};
    unwrap it when present. Any spawn/timeout/non-zero exit is logged and yields
    None (the caller treats None as "no records"). Factored small so tests
    monkeypatch sentinel_runner._dispatch without spawning a real claude.

    `tier` is the model tier to dispatch at (a sentinel def may declare its own via
    `model_tier`); it falls back to SENTINEL_MODEL_TIER. Interpretive sentinels
    (movement-watch: attribute each signal to one program) warrant a deeper tier
    than mechanical ones (tracker-truth: read adapter facts).

    `timeout` overrides CLAUDE_TIMEOUT when the sentinel definition declares one.
    """
    effective_timeout = timeout or CLAUDE_TIMEOUT
    model = profile_lib.resolve_model(tier or SENTINEL_MODEL_TIER)
    env = platform_lib.headless_claude_env()
    cmd = [
        platform_lib.resolve_claude(), "-p", prompt,
        "--model", model, "--output-format", "json",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=PM_OS_DIR, env=env,
            capture_output=True, text=True, timeout=effective_timeout,
        )
    except FileNotFoundError:
        log("'claude' not found on PATH - skipping (no observations recorded)")
        return None
    except subprocess.TimeoutExpired:
        log(f"claude timed out after {effective_timeout}s - no observations recorded")
        return None
    if proc.returncode != 0:
        log(f"claude exited {proc.returncode}: {proc.stderr.strip()[:300]}")
        return None
    out = (proc.stdout or "").strip()
    try:
        envelope = json.loads(out)
        if isinstance(envelope, dict) and "result" in envelope:
            return envelope["result"]
    except (json.JSONDecodeError, ValueError):
        pass
    return out


# --- The adapter-config seam (Task 4 wires the real check) -------------------

def _adapter_configured(name, root=None):
    """Whether the project-management adapter backing this sentinel is configured.

    Real check (Task 4): resolve the family's provider via adapters.get (which is
    None when no provider / the adaptation is off) and confirm its is_configured.
    Kept as a small module-level seam so the no-op branch and the mechanical branch
    are both monkeypatchable in tests. `name` is the sentinel name (room for
    per-sentinel adapter routing later).
    """
    mod = adapters.get(_ADAPTER_FAMILY, root)
    return bool(mod and mod.is_configured(root))


def _sheet_configured(name, root=None):
    """Whether the read-only EOS sheet locator is configured (profile-driven).

    A small module-level seam (like _adapter_configured) so the blind no-op is
    monkeypatchable in tests. `name` is the sentinel name (room for per-sentinel
    sheet routing later). Unconfigured -> the sheet sentinel runs blind."""
    return bool(profile_lib.eos_sheet(root))


# --- Processed-sources manifest (Layer 1 dedup) ----------------------------
#
# Tracks which source files a sentinel has already processed per program.
# After recording observations, the sentinel runner marks the source files as
# processed. On subsequent runs, processed files are listed in the prompt so
# the LLM skips them. This prevents the root cause of observation duplication:
# an LLM re-reading the same transcript and paraphrasing it differently.

def _processed_sources_path(root):
    return os.path.join(root or os.getcwd(), "datasets", "cadence", "processed-sources.json")


def read_processed_sources(sentinel_name, root=None):
    """Return {program_id: [file_stems]} for a sentinel, or {} if absent."""
    path = _processed_sources_path(root)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get(sentinel_name) or {}
    except (json.JSONDecodeError, IOError):
        return {}


def record_processed_sources(sentinel_name, new_sources, root=None):
    """Merge new_sources {program_id: [file_stems]} into the manifest.

    Deduplicates file stems per program, limits to 200 per program (LRU: oldest
    entries are dropped when the cap is hit). Never raises.
    """
    _MAX_PER_PROGRAM = 200
    root = root or os.getcwd()
    path = _processed_sources_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError, FileNotFoundError):
        data = {}
    sentinel_data = data.setdefault(sentinel_name, {})
    for pid, stems in new_sources.items():
        existing = sentinel_data.get(pid, [])
        merged = list(dict.fromkeys(existing + list(stems)))
        if len(merged) > _MAX_PER_PROGRAM:
            merged = merged[-_MAX_PER_PROGRAM:]
        sentinel_data[pid] = merged
    temp = path + ".tmp"
    try:
        with open(temp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(temp, path)
    except Exception as e:
        log(f"Failed to write processed-sources manifest: {e}")


def _extract_source_stems(records):
    """Extract {program_id: [normalized_file_stems]} from observation records.

    Uses program_lib._normalize_source_file for consistency with the Layer 2
    dedup in append_observation. Adapter sources are excluded (already
    deterministic). Never raises.
    """
    by_pid = {}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        pid = rec.get("program_id")
        source = rec.get("source", "")
        if not pid or not source:
            continue
        if source.strip().startswith("adapter:"):
            continue
        stem = program_lib._normalize_source_file(source)
        if stem:
            by_pid.setdefault(pid, []).append(stem)
    for pid in by_pid:
        by_pid[pid] = list(dict.fromkeys(by_pid[pid]))
    return by_pid


# --- Source gathering (thin, mockable; heavy qmd wiring deferred) ------------

_INCREMENTAL_FALLBACK_DAYS = 14


def _gather_sources(definition, programs, now=None, sentinel_name=None, root=None):
    """Return a plain-text in-window source digest for the prompt.

    When the sentinel has a recorded last_success in telemetry, the scan window
    starts 1 day before that date (overlap buffer; content-hash dedup prevents
    duplicates). On first run or after a failed run with no last_success, falls
    back to _INCREMENTAL_FALLBACK_DAYS.

    When a processed-sources manifest exists for this sentinel, the already-
    processed file stems are included in the prompt so the LLM skips them.
    """
    kinds = []
    for src in definition.get("sources") or []:
        if isinstance(src, dict) and src.get("kind"):
            kinds.append(str(src["kind"]))
    until = (now or program_lib._now_iso()[:10])
    kinds_line = ", ".join(kinds) if kinds else "(none declared)"

    since = None
    if sentinel_name:
        runs = read_sentinel_runs(root)
        last_ok = (runs.get(sentinel_name) or {}).get("last_success")
        if last_ok:
            try:
                last_date = date.fromisoformat(last_ok[:10])
                since = (last_date - timedelta(days=1)).isoformat()
            except (ValueError, TypeError):
                pass
    if since is None:
        try:
            end = date.fromisoformat(until[:10]) if isinstance(until, str) else until
            since = (end - timedelta(days=_INCREMENTAL_FALLBACK_DAYS)).isoformat()
        except (ValueError, TypeError):
            since = until

    processed_block = ""
    if sentinel_name:
        processed = read_processed_sources(sentinel_name, root)
        all_stems = set()
        for stems in processed.values():
            all_stems.update(stems)
        if all_stems:
            stems_list = "\n".join(f"  - {s}" for s in sorted(all_stems)[-50:])
            processed_block = (
                f"\nALREADY PROCESSED (skip these files entirely -- do NOT re-read "
                f"or emit observations from them):\n{stems_list}\n"
            )

    return (
        f"Scan window: sources dated between {since} and {until} (inclusive).\n"
        f"IMPORTANT: Only read transcripts dated ON OR AFTER {since}. "
        f"Skip anything older -- it was already processed.\n"
        f"Source kinds in scope: {kinds_line}.\n"
        "(In-window source contents are gathered by the dispatch tools; "
        f"read only what is in scope.){processed_block}"
    )


# --- Prompt assembly ---------------------------------------------------------

def _program_context(programs):
    """A compact, ASCII block of each active program's id + Intent for the prompt."""
    if not programs:
        return "(no active programs in scope)"
    blocks = []
    for prog in programs:
        pid = prog.get("program_id", "")
        intent = program_lib._parse_intent(prog.get("body") or "") or "(no intent recorded)"
        blocks.append(f"- {pid}\n  intent: {intent}")
    return "\n".join(blocks)


def _build_prompt(definition, programs, source_digest):
    """Assemble the dispatch prompt: sentinel body + program context + digest.

    The returned-records contract is restated so the dispatch is self-contained
    even if the def body is terse: a JSON array of objects with program_id, kind,
    source, claim, confidence. ASCII only (invariant #8).
    """
    body = str(definition.get("prompt") or "").strip()
    parts = [
        body,
        "\n=== ACTIVE PROGRAMS (attribute each signal to one or more of these ids; drop unattributable signals) ===",
        _program_context(programs),
        "\n=== SOURCES ===",
        source_digest,
        "\n=== RETURN FORMAT ===",
        "Return ONLY a JSON array of observation records and nothing else. Each "
        "record is an object with keys: program_id (one of the active ids above), "
        "kind, source, claim, confidence. Return [] if you found nothing worth "
        "recording.",
    ]
    return "\n".join(parts)


# --- Defensive JSON parsing --------------------------------------------------

def _parse_records(text):
    """Parse the dispatch output into a list of record dicts, defensively.

    Accepts a bare JSON array, a fenced ```json block, or a JSON array embedded in
    surrounding prose. Returns [] (never raises) on any parse failure or when the
    payload is not a list. Non-dict elements are filtered out.

    A dict-wrapped payload (e.g. {"records": [...]}) is intentionally treated as
    "no records" rather than unwrapped: the prompt asks for a bare array, and the
    deterministic-safe failure mode is to record nothing, never to guess a key.
    """
    if not text or not str(text).strip():
        return []
    cleaned = re.sub(r"^```(?:json)?|```$", "", str(text).strip(),
                     flags=re.MULTILINE).strip()
    obj = None
    for candidate in (cleaned, str(text)):
        try:
            obj = json.loads(candidate)
            break
        except (json.JSONDecodeError, ValueError):
            m = re.search(r"\[.*\]", candidate, re.DOTALL)
            if m:
                try:
                    obj = json.loads(m.group(0))
                    break
                except (json.JSONDecodeError, ValueError):
                    continue
    if not isinstance(obj, list):
        return []
    return [r for r in obj if isinstance(r, dict)]


# --- The mechanical (adapter-grounded) path ----------------------------------

def _program_tracker_epic(program):
    """The program's tracker anchor (project-management ref), or None.

    Delegates to the shared program_lib.tracker_anchor (the I1 seam): seed
    programs carry the tracker ref under `bindings[]` (role=truth,
    kind=project_management -> anchor), with the legacy `links.tracker_epic` as
    a fallback. A program with neither is skipped (returns None).
    """
    fm = (program or {}).get("frontmatter", {}) or {}
    return program_lib.tracker_anchor(fm)


def _map_tracker_fact(fact):
    """Map a tracker fact dict mechanically to a list of (kind, claim) pairs.

    A done/closed status -> completion; anything else -> status-signal. EA/GA
    dates, when present, each produce an additional date-change observation so
    Cadence can track release milestones.
    """
    records = []
    status = str(fact.get("status") or "").strip()
    if status.lower() in _DONE_STATUSES:
        kind = "completion"
    else:
        kind = "status-signal"
    title = str(fact.get("title") or "").strip()
    claim = f"Tracker reports status '{status}'"
    if title:
        claim += f" for '{title}'"
    claim += "."
    records.append((kind, claim))

    ea = fact.get("ea_date")
    if ea:
        records.append(("date-change", f"EA date is {ea}."))
    ga = fact.get("ga_date")
    if ga:
        records.append(("date-change", f"GA date is {ga}."))

    return records


def _run_tracker_truth(name, programs, root=None, now=None):
    """Mechanical tracker-truth: read adapter facts, map deterministically, append.

    Does NOT call the LLM. For each active program carrying links.tracker_epic,
    read the epic via the FREE adapters.fetch_status (a read of the system of
    record - never Tier-2). None (not found / unconfigured / off) -> skip. Map the
    fact to a kind, cite the epic as the source, append via the validated writer
    (its dedupe is the second fence). Returns the summary.
    """
    summary = {"sentinel": name, "appended": 0, "dropped": 0}
    obs_date = now or program_lib._now_iso()[:10]
    any_fact = False
    for prog in programs:
        pid = prog.get("program_id")
        epic = _program_tracker_epic(prog)
        if not pid or not epic:
            continue
        try:
            fact = adapters.fetch_status(_ADAPTER_FAMILY, epic, root=root)
        except Exception as exc:  # one misbehaving epic must not abort the run
            log(f"sentinel '{name}': adapter read for {epic} failed: {exc}")
            continue
        if not fact:
            continue
        any_fact = True
        records = _map_tracker_fact(fact)
        for kind, claim in records:
            try:
                appended = program_lib.append_observation(
                    pid, kind=kind, sentinel=name,
                    source=f"adapter:{_ADAPTER_FAMILY}:{epic}",
                    claim=claim, date=obs_date, root=root,
                )
            except (ValueError, TypeError, FileNotFoundError) as exc:
                log(f"sentinel '{name}': adapter record for {pid} rejected: {exc}")
                summary["dropped"] += 1
                continue
            if appended:
                summary["appended"] += 1
            else:
                summary["dropped"] += 1
    if not any_fact:
        log(f"sentinel '{name}': project_management adapter returned no facts - "
            "clean no-op (0 observations)")
    else:
        log(f"sentinel '{name}': appended {summary['appended']}, "
            f"dropped {summary['dropped']} (mechanical)")
    return summary


# --- The intake (routing) path -----------------------------------------------

def _candidate_key(record):
    """A stable identity hint for a candidate record (anchor preferred, else title).

    Stored on the candidate for traceability only - the merge decision in
    program_lib.upsert_candidate rides anchor/title/link_to. ASCII-safe; never
    raises (empty title/anchor -> empty string).
    """
    anchor = str(record.get("anchor") or "").strip()
    if anchor:
        return anchor
    return str(record.get("title") or "").strip()


def _resolve_intake_program_id(programs):
    """The id of the single active program of type program-intake, or None.

    `programs` is the active-program list (already filtered to status=active).
    Robust to zero (no nursery -> None, the caller drops candidate routes) and to
    more than one (the first by program_id wins - list_programs is id-sorted, so
    this is deterministic, and a second intake program is a registry bug, not a
    crash). Reads type from frontmatter. Never raises.
    """
    for prog in programs:
        fm = prog.get("frontmatter") or {}
        if fm.get("type") == _INTAKE_PROGRAM_TYPE:
            return prog.get("program_id")
    return None


def _run_intake(name, programs, text, root=None, now=None):
    """Apply the intake sentinel's routing records deterministically.

    Parses the dispatch output exactly like the observation path (defensively,
    bad records dropped + counted, never raised). For each record, route by its
    `route` verb:
      - observe   -> append_observation(program_id, kind=<record kind>, ...);
                     DROP if program_id is empty or not an active program.
      - capture   -> append_observation(program_id, kind="capture", ...);
                     DROP if program_id is empty or not an active program.
      - candidate -> upsert_candidate on the resolved intake program; DROP (never
                     raise) if no program-intake program exists.
      - ignore    -> no-op (counted as neither appended nor dropped).
    An unknown route, or a record append_observation/upsert_candidate rejects
    (e.g. empty source/claim), is counted as dropped - one bad record never stops
    the run. The LLM never writes; this runner is the only writer (same fence as
    movement-watch).
    """
    summary = {"sentinel": name, "appended": 0, "dropped": 0}
    records = _parse_records(text)
    if not records:
        log(f"sentinel '{name}': no parseable records - 0 observations recorded")
        return summary

    active_ids = {p.get("program_id") for p in programs}
    intake_id = _resolve_intake_program_id(programs)
    obs_date = now or program_lib._now_iso()[:10]

    for record in records:
        route = record.get("route")
        if route == "ignore":
            continue  # no-op: neither appended nor dropped
        if route not in _INTAKE_ROUTES:
            summary["dropped"] += 1
            continue

        if route in ("observe", "capture"):
            pid = record.get("program_id")
            # Never force-attribute: drop unattributed or unknown-program records
            # (the same active-program fence the observation path uses).
            if not pid or pid not in active_ids:
                summary["dropped"] += 1
                continue
            kind = "capture" if route == "capture" else record.get("kind")
            try:
                appended = program_lib.append_observation(
                    pid, kind=kind, sentinel=name,
                    source=record.get("source"), claim=record.get("claim"),
                    confidence=record.get("confidence"), date=obs_date, root=root,
                )
            except (ValueError, TypeError, FileNotFoundError) as exc:
                log(f"sentinel '{name}': {route} record for {pid} rejected: {exc}")
                summary["dropped"] += 1
                continue
            summary["appended" if appended else "dropped"] += 1
            continue

        # route == "candidate": upsert into the nursery. No intake program -> drop.
        if not intake_id:
            log(f"sentinel '{name}': candidate dropped - no {_INTAKE_PROGRAM_TYPE} "
                "program exists")
            summary["dropped"] += 1
            continue
        try:
            program_lib.upsert_candidate(
                intake_id,
                candidate_key=_candidate_key(record),
                program_type=record.get("program_type"),
                title=record.get("title"),
                source=record.get("source"),
                claim=record.get("claim"),
                anchor=record.get("anchor"),
                link_to=record.get("link_to"),
                confidence=record.get("confidence"),
                # Optional explicit-declaration flag; coerce defensively so a
                # missing or non-bool field never raises (just truthy/falsey).
                declared=bool(record.get("declared")),
                sentinel=name,
                root=root,
            )
        except (ValueError, TypeError, FileNotFoundError) as exc:
            log(f"sentinel '{name}': candidate rejected: {exc}")
            summary["dropped"] += 1
            continue
        summary["appended"] += 1

    log(f"sentinel '{name}': appended {summary['appended']}, "
        f"dropped {summary['dropped']} (intake)")

    new_sources = _extract_source_stems(records)
    if new_sources:
        record_processed_sources(name, new_sources, root)

    return summary


# --- Sentinel run telemetry --------------------------------------------------

def _sentinel_runs_path(root):
    """Return path to the sentinel-runs.json telemetry file."""
    return os.path.join(root or os.getcwd(), "datasets", "cadence", "sentinel-runs.json")


def read_sentinel_runs(root=None):
    """Read sentinel run telemetry from sentinel-runs.json.

    Returns a dict: {sentinel_name: {last_run, last_success, last_emitted_count, last_error}}.
    Returns empty dict if file missing or malformed.
    """
    path = _sentinel_runs_path(root)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def record_sentinel_run(name, *, success, emitted_count, error=None, root=None, now=None):
    """Record a sentinel run in telemetry.

    Args:
        name: Sentinel name (e.g., "movement-watch")
        success: True if the run succeeded, False if errored
        emitted_count: Number of items emitted/processed in this run
        error: Error message if success=False
        root: Root directory (defaults to os.getcwd())
        now: ISO timestamp (defaults to current time)

    Atomically updates sentinel-runs.json with:
    - last_run: always updated to now
    - last_success: updated only if success=True
    - last_emitted_count: always updated to emitted_count
    - last_error: updated if error is provided
    """
    root = root or os.getcwd()
    now = now or program_lib._now_iso()

    # Read current telemetry
    telemetry = read_sentinel_runs(root)

    # Update or create entry for this sentinel
    if name not in telemetry:
        telemetry[name] = {}

    entry = telemetry[name]
    entry["last_run"] = now
    if success:
        entry["last_success"] = now
    entry["last_emitted_count"] = emitted_count
    if error is not None:
        entry["last_error"] = error
    else:
        entry.pop("last_error", None)  # clear error if success

    # Atomic write
    path = _sentinel_runs_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    temp_path = path + ".tmp"
    with open(temp_path, "w") as f:
        json.dump(telemetry, f, indent=2)
    os.replace(temp_path, path)


# --- The run -----------------------------------------------------------------

def _run_sentinel_impl(name, root=None, now=None):
    """Internal implementation of sentinel run. Handles dispatch and execution.

    Returns a summary {"sentinel": name, "appended": N, "dropped": M}. Never
    raises out of a single bad record or a bad run - the program file is the
    system of record and a sentinel must never corrupt it.

    `now` (an ISO date string) pins BOTH the source scan-window in _gather_sources
    AND the observation entry date, so a test can drive the run deterministically;
    in production it is None and both default to today (UTC).

    Flow:
      - load the def; resolve active programs + their Intent as the scope.
      - if the sentinel is adapter-grounded (all sources are project_management):
        run the MECHANICAL path (_run_tracker_truth) - no LLM dispatch. An
        unconfigured adapter is a clean no-op (log once, empty summary).
      - otherwise (transcript sentinels like movement-watch):
        build the prompt, dispatch via _dispatch, parse records defensively.
      - for each record: DROP (count, do not append) when program_id is empty or
        is not an active program id (never force-attribute). Otherwise call
        append_observation; its validation + dedupe is the second fence - a
        ValueError (e.g. bad kind) or a False return (dedupe) counts as dropped,
        a True return counts as appended.
    """
    summary = {"sentinel": name, "appended": 0, "dropped": 0}

    # Sentinel defs are ENGINE artifacts (scripts/sentinels/*.md), resolved
    # against the repo - NOT under the datasets `root` (which scopes the program
    # files). This mirrors how program_lib loads the registry from the engine
    # while reading programs from `root`. So load_sentinel takes no `root`.
    try:
        definition = sentinel_lib.load_sentinel(name)
    except (FileNotFoundError, ValueError) as exc:
        log(f"sentinel '{name}' could not be loaded: {exc}")
        # A def that will not load means the sentinel did NOT run -> blind. Mark
        # the summary so run_sentinel records success=False (the silent-archive
        # door must not treat a blind sentinel as live). NOTE (deferred): a run
        # that DID dispatch but returned zero records is intentionally treated as
        # live -- a quiet sentinel over a genuinely dormant program is exactly the
        # case the silent door exists to archive. Distinguishing a dispatch
        # FAILURE (claude missing/timeout) from an empty result needs _dispatch to
        # signal that; tracked for a later increment.
        summary["error"] = f"sentinel def could not be loaded: {exc}"
        return summary

    programs = program_lib.list_programs(status="active", root=root)
    active_ids = {p.get("program_id") for p in programs}

    # Adapter-grounded sentinel (e.g. tracker-truth) runs the MECHANICAL path, not
    # the LLM dispatch path: it reads tracker facts and maps them deterministically.
    # A sentinel counts as adapter-grounded only when EVERY source it declares is
    # the project_management kind (a mixed sentinel still dispatches). Unconfigured
    # adapter -> clean no-op (log once, empty summary), never a dispatch.
    sources = definition.get("sources") or []
    source_kinds = {
        src.get("kind") for src in sources if isinstance(src, dict)
    }
    if source_kinds and source_kinds == {_ADAPTER_SOURCE_KIND}:
        if not _adapter_configured(name, root):
            log(f"sentinel '{name}': project_management adapter unconfigured - "
                "clean no-op (0 observations)")
            return summary
        return _run_tracker_truth(name, programs, root=root, now=now)

    # A sheet-backed sentinel (all sources eos_sheet) reads a manual-on-purpose
    # external sheet LIVE via the M365 MCP at dispatch. With no configured locator
    # it CANNOT read -> it is BLIND, not quiet-but-live: mark the summary error so
    # run_sentinel records success=False. (Distinguishing a configured-but-
    # MCP-absent dispatch from a genuinely empty read is the deferred
    # dispatch-failure-vs-empty nuance; configured -> dispatch and trust the read.)
    if source_kinds and source_kinds == {_SHEET_SOURCE_KIND}:
        if not _sheet_configured(name, root):
            log(f"sentinel '{name}': eos_sheet locator unconfigured - "
                "blind (0 observations)")
            summary["error"] = "eos_sheet source unconfigured (blind)"
            return summary
        # configured -> fall through to the normal LLM dispatch path below.

    source_digest = _gather_sources(definition, programs, now=now,
                                     sentinel_name=name, root=root)
    prompt = _build_prompt(definition, programs, source_digest)

    def_timeout = definition.get("timeout")
    text = _dispatch(prompt, tier=definition.get("model_tier"),
                     timeout=int(def_timeout) if def_timeout else None)

    if text is None:
        summary["error"] = "dispatch failed (timeout or process error)"
        return summary

    # The intake sentinel returns ROUTING records (observe/capture/candidate/
    # ignore) instead of program-attributed observations. It dispatches the LLM
    # exactly like movement-watch (transcript source), but the runner applies its
    # records by route rather than attributing each to a program. movement-watch
    # and tracker-truth are untouched by this branch.
    if name == _INTAKE_SENTINEL:
        return _run_intake(name, programs, text, root=root, now=now)

    records = _parse_records(text)
    if not records:
        log(f"sentinel '{name}': no parseable records - 0 observations recorded")
        return summary

    obs_date = now or program_lib._now_iso()[:10]
    for record in records:
        pid = record.get("program_id")
        # Never force-attribute: drop unattributed or unknown-program records.
        if not pid or pid not in active_ids:
            summary["dropped"] += 1
            continue
        try:
            appended = program_lib.append_observation(
                pid,
                kind=record.get("kind"),
                sentinel=name,
                source=record.get("source"),
                claim=record.get("claim"),
                confidence=record.get("confidence"),
                date=obs_date,
                root=root,
            )
        except (ValueError, TypeError, FileNotFoundError) as exc:
            # Second fence rejected this record (bad kind / empty source/claim /
            # missing program). Count it, keep going - one bad record never stops
            # the run.
            log(f"sentinel '{name}': record for {pid} rejected: {exc}")
            summary["dropped"] += 1
            continue
        if appended:
            summary["appended"] += 1
        else:
            # Dedupe (already on the program) - count as dropped.
            summary["dropped"] += 1

    log(f"sentinel '{name}': appended {summary['appended']}, "
        f"dropped {summary['dropped']}")

    # Record processed source files so subsequent runs skip them.
    new_sources = _extract_source_stems(records)
    if new_sources:
        record_processed_sources(name, new_sources, root)

    return summary


def run_sentinel(name, root=None, now=None):
    """Run a sentinel and record telemetry.

    Args:
        name: Sentinel name
        root: Root directory
        now: Optional timestamp override (for testing)

    Returns the summary dict from the sentinel run.
    Telemetry is always recorded (success or error).
    """
    root = root or os.getcwd()
    # The telemetry stamp is a full ISO timestamp. It must stay SEPARATE from the
    # `now` passed to the impl: the impl treats `now` as a YYYY-MM-DD date (the
    # observation date AND the source scan-window), so passing a full timestamp
    # through would malform observation headers. Pass `now` verbatim (None -> the
    # impl applies its own [:10] default).
    stamp = program_lib._now_iso()

    try:
        summary = _run_sentinel_impl(name, root=root, now=now)
        # A summary carrying an `error` means the sentinel did not actually run
        # (e.g. its def would not load) -> record it as a failed run so the
        # silent-archive door reads it as blind, not live.
        err = (summary or {}).get("error")
        emitted_count = summary.get("appended", 0) if summary else 0
        record_sentinel_run(name, success=(err is None), emitted_count=emitted_count,
                            error=err, root=root, now=stamp)
        return summary
    except Exception as e:
        # Record failure
        record_sentinel_run(name, success=False, emitted_count=0, error=str(e), root=root, now=stamp)
        # Return summary indicating failure (preserve "never raises" contract)
        return {"sentinel": name, "appended": 0, "dropped": 0, "error": str(e)}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run a Cadence sentinel and append its observations.")
    parser.add_argument("name", help="sentinel name (e.g. movement-watch)")
    parser.add_argument("--root", default=None,
                        help="PM-OS datasets root (defaults to the engine repo)")
    args = parser.parse_args(argv)
    summary = run_sentinel(args.name, root=args.root)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
