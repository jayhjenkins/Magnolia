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
from datetime import datetime, timezone

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
CLAUDE_TIMEOUT = 300

# The source kind that is grounded in the project-management adapter, and the
# adapter family it resolves to. A sentinel whose sources are ALL this kind runs
# the mechanical (adapter-read) path, NOT the LLM dispatch path; it degrades to a
# clean no-op when the adapter is unconfigured (tracker-truth today).
_ADAPTER_SOURCE_KIND = "project_management"
_ADAPTER_FAMILY = "project_management"

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

def _dispatch(prompt, tier=None):
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

    Task 4 extends the tracker-truth path to feed adapter-grounded facts into the
    prompt this dispatches; the seam shape (prompt in, text out) is unchanged.
    """
    model = profile_lib.resolve_model(tier or SENTINEL_MODEL_TIER)
    env = platform_lib.headless_claude_env()
    cmd = [
        platform_lib.resolve_claude(), "-p", prompt,
        "--model", model, "--output-format", "json",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=PM_OS_DIR, env=env,
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
        )
    except FileNotFoundError:
        log("'claude' not found on PATH - skipping (no observations recorded)")
        return None
    except subprocess.TimeoutExpired:
        log(f"claude timed out after {CLAUDE_TIMEOUT}s - no observations recorded")
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


# --- Source gathering (thin, mockable; heavy qmd wiring deferred) ------------

def _gather_sources(definition, programs, now=None):
    """Return a plain-text in-window source digest for the prompt.

    Deliberately thin this task: it states the scan window and lists the source
    kinds the sentinel is allowed to read, so the dispatched prompt is coherent
    and the runner's contract is testable without real qmd/transcript ingestion.
    The real in-window transcript pull (qmd) and the adapter fact pull arrive in
    later tasks; this stays a small, mockable seam so they slot in without
    changing run_sentinel's shape. ASCII-safe.
    """
    kinds = []
    for src in definition.get("sources") or []:
        if isinstance(src, dict) and src.get("kind"):
            kinds.append(str(src["kind"]))
    window = (now or program_lib._now_iso()[:10])
    kinds_line = ", ".join(kinds) if kinds else "(none declared)"
    return (
        f"Scan window: sources updated on or before {window}.\n"
        f"Source kinds in scope: {kinds_line}.\n"
        "(In-window source contents are gathered by the dispatch tools; "
        "read only what is in scope.)"
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
        "\n=== ACTIVE PROGRAMS (attribute each signal to ONE of these ids, or drop it) ===",
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
    """Map a tracker fact dict mechanically to (kind, claim). No interpretation.

    A done/closed status -> completion; anything else -> status-signal. The claim
    is a short ASCII restatement of the reported status. (date-change is left to a
    later pass - comparing the fetched due to a checkpoint due cleanly needs the
    checkpoint match, and emitting it speculatively would not be mechanical.)
    """
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
    return kind, claim


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
        kind, claim = _map_tracker_fact(fact)
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
    return summary


# --- The run -----------------------------------------------------------------

def run_sentinel(name, root=None, now=None):
    """Run one sentinel: dispatch, parse records, append the attributed ones.

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

    source_digest = _gather_sources(definition, programs, now=now)
    prompt = _build_prompt(definition, programs, source_digest)

    text = _dispatch(prompt, tier=definition.get("model_tier"))

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
    return summary


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
