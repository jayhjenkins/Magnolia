#!/usr/bin/env python3
"""
reconcile.py - The deterministic Cadence reconciler (pure verdict core).

Task 1 of the reconcile-engine increment: the no-I/O, no-write core only.
Everything here takes an injected `now` and returns data - no `date.today()`,
no file writes, no emitters, no scheduler (those land in later tasks).

The verdict per program is computed deterministically from declared-vs-observed
state, dispatched on the program type's `state_model` (resolved from the
registry exactly as program_lib.render_view does). The uniform output set is
{holding, drifting, broken}; worst signal wins. Missing or unparseable data
contributes nothing (degrades to holding) and NEVER raises - real seed
checkpoints mix ISO dates and human strings like "Thu 9:00am".

All runtime-produced strings are ASCII-safe (hyphen, never em-dash) per
invariant #8.
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import program_lib

# Worst-signal-wins ordering: broken beats drifting beats holding.
_VERDICTS = ("holding", "drifting", "broken")
_RANK = {v: i for i, v in enumerate(_VERDICTS)}

# Threshold constants - named per the sibling program_lib._SERIES_TOL convention.
_SOON_WINDOW_DAYS = 7      # a pending checkpoint due within this window -> drifting
_DRIFT_FRACTION = 0.8      # >this fraction of an age/policy limit -> drifting
_DEFAULT_POLICY_DAYS = 14  # register item age window when none is declared
_DEFAULT_TOLERANCE = 8     # target series tolerance when none is declared
_BROKEN_MULTIPLIER = 2     # diff beyond this multiple of tolerance -> broken


def _worse(a, b):
    """Return the worse of two verdicts (broken > drifting > holding)."""
    return a if _RANK.get(a, 0) >= _RANK.get(b, 0) else b


# ─── Pure time helpers ─────────────────────────────────────────────────────────

def current_period(cadence, now):
    """Return the period key for a cadence at `now`.

    weekly / None / unknown -> ISO-week key `YYYY-Www` (from now.isocalendar()).
    daily -> ISO date `YYYY-MM-DD`. `now` may be a date or datetime.
    """
    if cadence == "daily":
        return _to_date(now).isoformat()
    # weekly (and any unknown cadence) falls back to the ISO-week key.
    iso = now.isocalendar()
    year, week = iso[0], iso[1]
    return f"{year}-W{week:02d}"


def _to_date(value):
    """Coerce a date/datetime to a date (datetime is a subclass of date)."""
    if isinstance(value, datetime):
        return value.date()
    return value


def _parse_iso_date(value):
    """Return a datetime.date for an ISO value, else None - never raises.

    Accepts a date, a datetime (returns .date()), or a "YYYY-MM-DD" string.
    Anything else - human strings ("Mon Jun 16", "Thu 9:00am"), None, ints -
    returns None. Load-bearing: seed checkpoints mix ISO dates and human
    strings on the same field.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


# ─── Verdict core ──────────────────────────────────────────────────────────────

def compute_verdict(program, registry, now):
    """Compute (verdict, facts) for a program at `now`.

    `program` is the read_program shape: {"frontmatter": {...}, "body": "..."}.
    `verdict` is one of {holding, drifting, broken}. `facts` is a small dict
    with at least {"reason", "next"} (short ASCII strings) describing the worst
    signal, used later for the cycle-log line and the card body.

    The state_model is resolved from the registry by the program's `type`,
    exactly like render_view. Unknown/empty state_model -> holding. The function
    never raises on bad or missing data - it degrades to holding.
    """
    fm = (program or {}).get("frontmatter", {}) or {}
    type_id = fm.get("type")
    type_entry = next(
        (t for t in registry.get("types", []) if t.get("id") == type_id), {}
    )
    state_model = type_entry.get("state_model")

    try:
        if state_model == "pipeline":
            return _verdict_pipeline(fm, type_entry, now)
        if state_model == "register":
            return _verdict_register(fm)
        if state_model == "target":
            return _verdict_target(fm)
        if state_model == "cycle":
            return _verdict_cycle(fm)
    except Exception:
        # Defensive: any unexpected shape degrades to holding, never raises.
        return "holding", {"reason": "no signal", "next": "none"}

    return "holding", {"reason": "no signal", "next": "none"}


def _verdict_pipeline(fm, type_entry, now):
    """pipeline: pending checkpoint due-dates + current-phase age window."""
    now_date = _to_date(now)
    verdict = "holding"
    facts = {"reason": "on track", "next": "none"}

    # Checkpoint signals.
    for cp in fm.get("checkpoints") or []:
        status = cp.get("status")
        label = cp.get("label") or cp.get("id") or "checkpoint"
        if status == "missed":
            if verdict != "broken":
                facts = {"reason": f"checkpoint missed: {label}",
                         "next": f"reset {label}"}
            verdict = _worse(verdict, "broken")
            continue
        if status == "met":
            continue  # not pending - no signal
        # pending (or any other non-terminal status): judge the due date.
        due = _parse_iso_date(cp.get("due"))
        if due is None:
            continue  # human-string / missing due contributes nothing
        if due < now_date:
            overdue = (now_date - due).days
            if verdict != "broken":
                facts = {"reason": f"{label} overdue {overdue}d",
                         "next": f"ship {label}"}
            verdict = _worse(verdict, "broken")
        elif due <= now_date + timedelta(days=_SOON_WINDOW_DAYS):
            remaining = (due - now_date).days
            if verdict == "holding":
                facts = {"reason": f"{label} due in {remaining}d",
                         "next": f"prep {label}"}
            verdict = _worse(verdict, "drifting")

    # Current-phase age window.
    phase = fm.get("phase")
    phase_def = next(
        (p for p in (type_entry.get("phases") or []) if p.get("id") == phase),
        {},
    )
    max_age = phase_def.get("max_age_days")
    entered = _phase_entered_date(fm, phase)
    if max_age and entered is not None:
        days_in_phase = (now_date - entered).days
        if days_in_phase > max_age:
            if verdict != "broken":
                facts = {"reason": f"phase {phase} {days_in_phase}d over limit",
                         "next": f"advance {phase}"}
            verdict = _worse(verdict, "broken")
        elif days_in_phase > _DRIFT_FRACTION * max_age:
            if verdict == "holding":
                facts = {"reason": f"phase {phase} aging ({days_in_phase}d)",
                         "next": f"watch {phase}"}
            verdict = _worse(verdict, "drifting")

    return verdict, facts


def _phase_entered_date(fm, phase):
    """Return the date the current phase was entered, or None.

    Tolerates the dict form {phase_id: date} (seed) and a scalar date (brief
    form = the date the CURRENT phase was entered), mirroring render_view.
    """
    raw = fm.get("phase_entered")
    if isinstance(raw, dict):
        return _parse_iso_date(raw.get(phase))
    if raw:
        return _parse_iso_date(raw)
    return None


def _verdict_register(fm):
    """register: each item's age vs the policy window (default 14 days)."""
    policy = fm.get("policy", _DEFAULT_POLICY_DAYS)
    try:
        policy = int(policy)
    except (TypeError, ValueError):
        policy = _DEFAULT_POLICY_DAYS
    items = fm.get("items") or []
    verdict = "holding"
    facts = {"reason": "within policy", "next": "none"}
    for it in items:
        age = it.get("age")
        if not isinstance(age, (int, float)):
            continue
        name = it.get("name") or "item"
        if age > policy:
            if verdict != "broken":
                facts = {"reason": f"{name} aged {age}d (policy {policy})",
                         "next": f"clear {name}"}
            verdict = _worse(verdict, "broken")
        elif age > _DRIFT_FRACTION * policy:
            if verdict == "holding":
                facts = {"reason": f"{name} aging {age}d (policy {policy})",
                         "next": f"watch {name}"}
            verdict = _worse(verdict, "drifting")
    return verdict, facts


def _verdict_target(fm):
    """target: latest actual vs expected predicted, against tolerance."""
    series = fm.get("series") or {}
    act = series.get("act") or []
    pred = series.get("pred") or []
    if not act or not pred:
        return "holding", {"reason": "no series", "next": "none"}
    # Compare the latest actual against its predicted counterpart. When actuals
    # run past predictions (a longer act series), the index pins to the last
    # prediction - the final predicted point is the standing expectation.
    expected = pred[min(len(act) - 1, len(pred) - 1)]
    actual = act[-1]
    tol = (fm.get("metric") or {}).get("tolerance", _DEFAULT_TOLERANCE)
    try:
        diff = abs(actual - expected)
    except TypeError:
        return "holding", {"reason": "no series", "next": "none"}
    if diff > _BROKEN_MULTIPLIER * tol:
        return "broken", {"reason": f"actual {actual} vs expected {expected}",
                          "next": "diagnose metric"}
    if diff > tol:
        return "drifting", {"reason": f"actual {actual} vs expected {expected}",
                            "next": "watch metric"}
    return "holding", {"reason": f"actual {actual} vs expected {expected}",
                       "next": "none"}


def _verdict_cycle(fm):
    """cycle: the latest period's status (sent/late/missed)."""
    periods = fm.get("periods") or []
    if not periods:
        return "holding", {"reason": "no periods", "next": "none"}
    latest = periods[-1].get("s")
    week = periods[-1].get("w") or "latest"
    if latest == "missed":
        return "broken", {"reason": f"{week} missed", "next": "send digest"}
    if latest == "late":
        return "drifting", {"reason": f"{week} late", "next": "send on time"}
    return "holding", {"reason": f"{week} sent", "next": "none"}


# ─── Stateful one-program cycle (Tasks 2 + 3) ────────────────────────────────
#
# reconcile_program runs ONE program's cycle: compute the verdict, guard it to
# once-per-cadence-period, and on a fresh cycle evaluate the type's declarative
# emitters, write the verdict back into the frontmatter (drift/last_cycle/
# last_run), and append a `## Cycles` log entry to the body. Writes go through
# program_lib._write_program_file (its YAML validation + revert gate).
# Append-only: prior `## Cycles` entries and the `## Observations` section are
# never rewritten (invariant #6). The cycle header uses an ASCII hyphen, never
# an em-dash (invariant #8).
#
# Emitters are DECLARATIVE: the type's registry `emitters` list maps a verdict
# (`on: drift:<verdict>`) to an `action`. Only `escalate` is acted on this
# increment — it creates a local human-queue card (Tier-1, no external writes,
# no judge/ladder). Other closed-set actions are recognized but no-op'd.

_CYCLES_HEADING = "## Cycles"


def _resolve_cadence(fm, registry):
    """Resolve a program's cadence: frontmatter -> registry type -> 'weekly'."""
    cadence = fm.get("cadence")
    if cadence:
        return cadence
    type_id = fm.get("type")
    type_entry = next(
        (t for t in registry.get("types", []) if t.get("id") == type_id), {}
    )
    return type_entry.get("cadence") or "weekly"


def _format_emitted(emitted):
    """Render the cycle-log `emitted:` clause (ASCII, invariant #8).

    `emitted: TASK-0123` (or comma-joined for several), `emitted: none` when no
    cards fired.
    """
    ids = [tid for tid in (emitted or []) if tid]
    if not ids:
        return "none"
    return ", ".join(ids)


def _append_cycle_entry(body, period, verdict, facts, emitted=None):
    """Return `body` with a new cycle-log entry appended to `## Cycles`.

    The entry is two lines (ASCII hyphen separators, invariant #8):

        ### <period> - <verdict>
        checks: <reason> - emitted: <TASK-xxxx | none> - next: <next>

    Heading-anchored insert: the new block is placed UNDER the `## Cycles`
    heading and BEFORE the next top-level `## ` heading (if any), else at the
    end of the section. A section that follows `## Cycles` is preserved verbatim
    and stays after the cycle entries. Append-only: an existing `## Cycles`
    section keeps every prior entry, in order (invariant #6). If `## Cycles` is
    absent it is created at the end of the body. A blank line separates entries
    so the markdown stays re-readable.
    """
    reason = (facts or {}).get("reason", "none")
    nxt = (facts or {}).get("next", "none")
    entry = (
        f"### {period} - {verdict}\n"
        f"checks: {reason} - emitted: {_format_emitted(emitted)} - next: {nxt}\n"
    )

    body = body or ""
    if _CYCLES_HEADING not in body:
        # Create the section at the end of the body.
        base = body.rstrip("\n")
        if base:
            return f"{base}\n\n{_CYCLES_HEADING}\n\n{entry}"
        return f"{_CYCLES_HEADING}\n\n{entry}"

    # Anchor on the LAST `## Cycles` heading. The head (everything up to and
    # including the heading) is preserved verbatim. The trailing text is split
    # at the next top-level `## ` heading: `section` holds the Cycles content
    # (all prior entries), `rest` holds any following section(s) preserved as-is.
    # The new entry lands at the end of `section`, before `rest`.
    head, sep, tail = body.rpartition(_CYCLES_HEADING)
    section, rest = _split_at_next_section(tail)
    section = section.rstrip("\n")
    if rest:
        return f"{head}{sep}{section}\n\n{entry}\n{rest}"
    return f"{head}{sep}{section}\n\n{entry}"


def _split_at_next_section(text):
    """Split `text` at the next top-level `## ` heading.

    Returns (section, rest): `section` is the Cycles content up to the next
    top-level heading line; `rest` is that heading and everything after it (or
    "" when no following section exists). The Cycles heading's own `### ` entry
    sub-headers are NOT top-level, so they stay in `section`.
    """
    idx = text.find("\n## ")
    if idx == -1:
        return text, ""
    return text[:idx], text[idx + 1:]


def _build_card_description(facts, program_id):
    """Build a <=2-sentence ASCII card body from facts + the program backlink.

    `facts` carries the worst-signal {reason, next}; the backlink is the
    program_id (the dedupe/render tag and how a reader navigates back).
    """
    reason = (facts or {}).get("reason", "needs attention")
    nxt = (facts or {}).get("next", "review")
    return f"Cadence flagged {program_id} as broken: {reason}. Next: {nxt}."


def _open_human_tags(task_lib):
    """Return the set of tags carried by OPEN human-queue tasks (escalate fence)."""
    tags = set()
    for t in task_lib.list_tasks(queue="human", status="open"):
        for tag in t.get("tags", []) or []:
            tags.add(tag)
    return tags


def _evaluate_emitters(program, type_entry, verdict, facts, root=None):
    """Evaluate the type's declarative emitters for `verdict`. Returns task ids.

    For each emitter whose `on` matches `drift:<verdict>`:
      - `escalate`: dedupe against open human cards already tagged with this
        program_id; if none, create one high-priority human card tagged
        [program_id, "cadence"] and collect its id. (Tier-1: a LOCAL card, no
        external writes, no judge/ladder.)
      - any other (recognized) action: no-op this increment (logged to stderr).
    """
    emitters = type_entry.get("emitters") or []
    if not emitters:
        return []

    fm = program["frontmatter"]
    program_id = fm.get("program_id")
    title = fm.get("title") or program_id or "Program"
    emitted = []
    open_tags = None  # lazily computed only when an escalate emitter fires

    for em in emitters:
        if em.get("on") != f"drift:{verdict}":
            continue
        action = em.get("action")
        if action == "escalate":
            # Lazy import on the escalate path only - the pure-verdict path
            # never imports task_lib. Imported once here, not again per card.
            import task_lib
            if open_tags is None:
                open_tags = _open_human_tags(task_lib)
            if program_id in open_tags:
                continue  # an open card already covers this program -> dedupe
            task_id, _ = task_lib.create_task(
                title=f"{title} needs attention",
                queue="human",
                priority="high",
                creator="cadence",
                tags=[program_id, "cadence"],
                description=_build_card_description(facts, program_id),
            )
            emitted.append(task_id)
            # Reflect the just-created card so a second escalate emitter in the
            # same evaluation cannot double-fire for this program.
            open_tags.add(program_id)
        elif action:
            sys.stderr.write(
                f"[cadence] emitter action '{action}' not acted on this "
                f"increment ({program_id})\n"
            )

    return emitted


def reconcile_program(program, registry, now=None, force=False, root=None):
    """Run one program's reconcile cycle. Returns a result dict.

    `program` is the read_program shape ({"frontmatter", "body", "filepath"}).
    Computes the verdict, then guards to once-per-cadence-period: if this
    program already ran this period (and not `force`), returns without touching
    the file. On a fresh cycle, evaluates the type's declarative emitters
    (the escalate card fires here, deduped), writes drift/last_cycle/last_run
    back into the frontmatter, appends a `## Cycles` log entry recording any
    emitted card ids, and writes the file ONCE via _write_program_file.

    Returns {"program_id", "verdict", "new_cycle": bool, "emitted": [ids]}.
    May raise on a genuinely unwritable file; reconcile_all (Task 4) wraps it.
    """
    now = now or datetime.now(timezone.utc)
    fm = program["frontmatter"]
    body = program["body"]

    verdict, facts = compute_verdict(program, registry, now)

    cadence = _resolve_cadence(fm, registry)
    period = current_period(cadence, now)

    is_new_cycle = force or fm.get("last_cycle") != period

    if not is_new_cycle:
        return {
            "program_id": fm.get("program_id"),
            "verdict": verdict,
            "new_cycle": False,
            "emitted": [],
        }

    # Fresh cycle: evaluate emitters FIRST (so the cycle log can record them),
    # THEN write verdict back + append the cycle log, in ONE program-file write.
    type_id = fm.get("type")
    type_entry = next(
        (t for t in registry.get("types", []) if t.get("id") == type_id), {}
    )
    emitted = _evaluate_emitters(program, type_entry, verdict, facts, root=root)

    fm["drift"] = verdict
    fm["last_cycle"] = period
    fm["last_run"] = program_lib._now_iso()
    body = _append_cycle_entry(body, period, verdict, facts, emitted=emitted)

    filepath = program.get("filepath")
    if not filepath:
        filepath = os.path.join(
            program_lib._program_dir(root), f"{fm['program_id']}.md"
        )
    program_lib._write_program_file(filepath, fm, body)

    return {
        "program_id": fm.get("program_id"),
        "verdict": verdict,
        "new_cycle": True,
        "emitted": emitted,
    }


# ─── Portfolio driver + CLI (Task 4) ─────────────────────────────────────────
#
# reconcile_all is the once-per-cadence-tick driver: load the registry ONCE,
# list the active programs, reconcile each inside try/except so one bad program
# (an unwritable file, a corrupt shape that slips past compute_verdict's
# degrade-to-holding guard) never stalls the whole run. Each program yields one
# result dict; a failure yields {"program_id", "error"} and the run continues.
# Tier-1: no scheduler here (Task 5), no new emitter actions, no external writes
# beyond the existing escalate card.


def reconcile_all(root=None, now=None, force=False):
    """Reconcile every ACTIVE program. Returns one result dict per program.

    Loads the registry once, lists `status="active"` programs (candidate/paused/
    archived are filtered out by list_programs), and reconciles each inside a
    try/except so one failure never stalls the run. On success appends the
    reconcile_program result; on exception logs a tagged line to stderr and
    appends {"program_id", "error": str(e)}, then continues.
    """
    registry = program_lib.load_registry()
    programs = program_lib.list_programs(status="active", root=root)

    results = []
    for program in programs:
        program_id = (program.get("frontmatter") or {}).get("program_id") or "?"
        try:
            results.append(
                reconcile_program(program, registry, now=now, force=force, root=root)
            )
        except Exception as e:  # one bad program must not stall the run
            sys.stderr.write(f"[cadence-reconcile] {program_id}: {e}\n")
            results.append({"program_id": program_id, "error": str(e)})
    return results


def _parse_now(value):
    """Parse an ISO timestamp (tolerating a trailing `Z`) into a datetime.

    Raises ValueError on a bad value so the CLI can report it; mirrors
    cron_lib's `Z`-to-offset normalization.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _summary_line(result):
    """Render a one-line ASCII summary for one reconcile result (invariant #8)."""
    program_id = result.get("program_id", "?")
    if "error" in result:
        return f"{program_id} ERROR: {result['error']}"
    verdict = result.get("verdict", "?")
    if result.get("new_cycle"):
        emitted = result.get("emitted") or []
        if emitted:
            cycle = f"new cycle, emitted {', '.join(emitted)}"
        else:
            cycle = "new cycle"
    else:
        cycle = "no change"
    return f"{program_id} {verdict} ({cycle})"


def main(argv=None):
    """CLI entrypoint. Returns a process exit code (0 on success)."""
    parser = argparse.ArgumentParser(
        prog="reconcile",
        description="Reconcile active Cadence programs (deterministic, Tier-1).",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="reconcile all active programs (required for now)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="rerun even if a program already reconciled this period",
    )
    parser.add_argument(
        "--now", metavar="ISO",
        help="reconcile as of this ISO timestamp (a trailing Z is accepted)",
    )
    args = parser.parse_args(argv)

    if not args.all:
        sys.stderr.write("nothing to do: pass --all to reconcile active programs\n")
        return 2

    now = None
    if args.now:
        try:
            now = _parse_now(args.now)
        except ValueError:
            sys.stderr.write(
                f"error: could not parse --now value '{args.now}' "
                f"(expected ISO format, e.g. 2026-06-16T09:00:00Z)\n"
            )
            return 2

    results = reconcile_all(now=now, force=args.force)
    for result in results:
        print(_summary_line(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
