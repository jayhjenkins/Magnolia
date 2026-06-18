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
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import platform_lib
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


# Instrument normalization (I2). Seed checkpoint `instrument` fields are PROSE
# ("the PM tracker", "human attestation", "a deterministic check", "Pendo"), not
# tokens. The fact door may only auto-mutate state on a MECHANICAL instrument
# (an adapter/deterministic/metric source). A human-attestation or empty/unclear
# instrument is treated as human (return False) -- the conservative default, so
# the reconciler never auto-advances a phase on an instrument it cannot classify.
# Both sets are ASCII, matched case-insensitively. Human wins on conflict.
_HUMAN_INSTRUMENT_HINTS = ("human", "attest", "manual")
_MECHANICAL_INSTRUMENT_HINTS = (
    "tracker", "adapter", "pendo", "metric", "deterministic", "automated", "check",
)


def _instrument_is_mechanical(instrument):
    """Classify a prose `instrument` as mechanical (True) or human (False).

    Mechanical when it names an adapter/deterministic/metric source. Human when
    it names attestation OR is empty/ambiguous (CONSERVATIVE DEFAULT: unclear ->
    human, so the fact door never auto-mutates state on an instrument it cannot
    confidently read as mechanical). ASCII, case-insensitive; human hints win.
    """
    if not instrument or not isinstance(instrument, str):
        return False
    low = instrument.lower()
    if any(h in low for h in _HUMAN_INSTRUMENT_HINTS):
        return False
    return any(h in low for h in _MECHANICAL_INSTRUMENT_HINTS)


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


def _append_cycle_entry(body, period, verdict, facts, emitted=None, advanced=None):
    """Return `body` with a new cycle-log entry appended to `## Cycles`.

    The entry is two lines (ASCII hyphen separators, invariant #8), plus an
    optional third line when the fact door advanced a phase this cycle:

        ### <period> - <verdict>
        checks: <reason> - emitted: <TASK-xxxx | none> - next: <next>
        advanced: <from> -> <to> on checkpoint <id>

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
    if advanced:
        entry += (
            f"advanced: {advanced['from']} -> {advanced['to']} "
            f"on checkpoint {advanced['checkpoint']}\n"
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


def _open_propose_update_ops(task_lib, program_id):
    """Return the set of mutation `op`s already covered by an OPEN cadence
    propose-update card for `program_id` (the proposal dedupe fence).

    Mirrors _open_human_tags but reads two more fields off each open human card:
    its `task_type` (must be `cadence-propose-update`) and its `proposal` op. A
    card matches when it is a cadence-propose-update tagged with this program_id;
    we collect its `proposal.op` so a second reconcile cannot stack a duplicate
    advance-phase proposal while one is still open. Defensive: list_tasks only
    projects light fields, so we re-read the card frontmatter for `proposal`.
    Never raises - an unreadable card is simply skipped.
    """
    ops = set()
    for t in task_lib.list_tasks(queue="human", status="open"):
        if t.get("task_type") != "cadence-propose-update":
            continue
        if program_id not in (t.get("tags") or []):
            continue
        try:
            fm = task_lib.read_task(t["id"])["frontmatter"]
        except Exception:
            continue
        proposal = fm.get("proposal") or {}
        op = proposal.get("op") if isinstance(proposal, dict) else None
        if op:
            ops.add(op)
    return ops


def _open_birth_candidate_ids(task_lib, intake_program_id):
    """Return the set of candidate_ids already covered by an OPEN birth proposal.

    The birth-proposal dedupe fence. Mirrors _open_propose_update_ops but keys on
    `proposal.candidate_id` instead of `proposal.op`: every birth shares op
    "birth", so op-dedupe (correct for the single-op advance-phase door) would
    collapse all candidates into one and let only the first candidate ever get a
    proposal. We scan OPEN cadence-propose-update cards tagged with the intake
    program_id whose proposal op is "birth" and collect each proposal's
    candidate_id, so a second reconcile cannot stack a duplicate birth for a
    candidate while one is still open. Defensive: list_tasks projects only light
    fields, so we re-read each card's frontmatter for `proposal`; an unreadable
    card is skipped. Never raises.
    """
    ids = set()
    for t in task_lib.list_tasks(queue="human", status="open"):
        if t.get("task_type") != "cadence-propose-update":
            continue
        if intake_program_id not in (t.get("tags") or []):
            continue
        try:
            fm = task_lib.read_task(t["id"])["frontmatter"]
        except Exception:
            continue
        proposal = fm.get("proposal") or {}
        if not isinstance(proposal, dict) or proposal.get("op") != "birth":
            continue
        cid = proposal.get("candidate_id")
        if cid:
            ids.add(cid)
    return ids


def _birth_threshold_for(program_type, registry):
    """Return a target type's intake.birth_threshold dict, or {} when absent.

    Tolerant of a type with no `intake` block or no `birth_threshold` (returns an
    empty dict so the caller's ripeness test simply never fires). Never raises.
    """
    type_entry = next(
        (t for t in registry.get("types", []) if t.get("id") == program_type), {}
    )
    intake = type_entry.get("intake") or {}
    threshold = intake.get("birth_threshold") or {}
    return threshold if isinstance(threshold, dict) else {}


def _candidate_is_ripe(candidate, threshold):
    """True when a candidate's evidence crosses its target type's birth_threshold.

    Two ripeness paths, both read off the (already-validated) threshold dict:

      - source-counting: ripe when `min_independent_sources` is present AND the
        candidate's `source_count` meets it. This path is DISALLOWED for a
        declaration-only type (no min_independent_sources key), so a quarterly
        mention never source-counts its way to a birth.
      - explicit declaration: ripe when the candidate carries `declared: true`
        AND the threshold allows it (`or_explicit_declaration` OR
        `explicit_declaration_only`).

    The `declared` flag is the agreed lightweight representation of an explicit
    "we are committing to this" declaration on a candidate (default False/absent).
    upsert_candidate does NOT set it today; Task 5's intake sentinel / Task 7 can
    set it later from a recognized declaration. Until then a declaration-only type
    simply never births here -- which is the safe behavior (no premature births).
    Tolerant of non-dict / missing shapes; never raises.
    """
    if not isinstance(candidate, dict) or not isinstance(threshold, dict):
        return False

    min_sources = threshold.get("min_independent_sources")
    if isinstance(min_sources, int) and not isinstance(min_sources, bool):
        source_count = candidate.get("source_count")
        if isinstance(source_count, int) and not isinstance(source_count, bool):
            if source_count >= min_sources:
                return True

    declared = candidate.get("declared") is True
    allows_declaration = bool(
        threshold.get("or_explicit_declaration")
        or threshold.get("explicit_declaration_only")
    )
    return declared and allows_declaration


def _candidate_citations(candidate):
    """Return the DISTINCT evidence sources of a candidate, order-preserved.

    These are the citations that earned a birth (written into the new program's
    ## Intent at accept). Tolerant of malformed evidence; never raises.
    """
    seen = []
    for ev in candidate.get("evidence") or []:
        if not isinstance(ev, dict):
            continue
        src = (ev.get("source") or "").strip()
        if src and src not in seen:
            seen.append(src)
    return seen


def _propose_births(intake_fm, registry, body=None):
    """Return birth proposals for the OPEN, ripe candidates in an intake register.

    For each `status == "open"` candidate item under intake_fm["items"], look up
    its TARGET type's intake.birth_threshold (NOT the intake program's), and if
    the accumulated evidence is ripe (_candidate_is_ripe) emit a birth proposal:

        {op: "birth", program_type, title, candidate_id, checkpoints: [],
         citations: [distinct sources]}

    Checkpoints are left empty here -- a newborn's inferred checkpoints are the
    accept path's concern (4a marks them honestly as pending, no grounding sweep).
    `body` is accepted for signature symmetry with the other producers but unused
    (candidate state lives entirely in the frontmatter items). Proposal-only:
    never mutates the intake program. ASCII-safe; tolerant of malformed items.
    """
    proposals = []
    for cand in intake_fm.get("items") or []:
        if not isinstance(cand, dict):
            continue
        if cand.get("status") != "open":
            continue  # closed-with-reason / birthed / unknown -> never proposed
        program_type = cand.get("program_type")
        if not program_type:
            continue
        threshold = _birth_threshold_for(program_type, registry)
        if not _candidate_is_ripe(cand, threshold):
            continue
        proposals.append({
            "op": "birth",
            "program_type": program_type,
            "title": cand.get("title") or program_type,
            "candidate_id": cand.get("id"),
            "checkpoints": [],
            "citations": _candidate_citations(cand),
        })
    return proposals


def _resolve_nudge_target(fm, root=None):
    """Resolve a (channel, recipient) nudge target -- profile-driven, NO literal.

    The recipient must never be a person/company literal (invariant #1,
    test_engine_no_jay). We resolve the messaging CHANNEL from the profile's
    messaging integration (e.g. `m365`), degrading to `none` when unset, and the
    RECIPIENT to a generic ROLE-BASED target built from the program's
    `owner_role` (e.g. `product team`). owner_role is itself a role token, not an
    identity, so the resulting string carries no name. When a program declares no
    owner_role we fall back to a generic `program owner` target. ASCII-safe.
    """
    import profile_lib
    try:
        channel = profile_lib.provider("messaging", root=root)
    except Exception:
        channel = "none"
    role = (fm.get("owner_role") or "").strip()
    recipient = f"{role} team" if role else "program owner"
    return channel, recipient


def _build_nudge_description(facts, program_id, recipient):
    """Build a <=2-sentence ASCII nudge body (invariant #8).

    A polite, role-addressed nudge that cites the program backlink and the worst
    signal so the recipient knows why they are being pinged. ASCII, no em-dash.
    """
    reason = (facts or {}).get("reason", "needs a look")
    nxt = (facts or {}).get("next", "review")
    return (
        f"Nudge for {recipient}: {program_id} {reason}. Next: {nxt}. "
        f"Reply or update the program to clear this."
    )


def _record_nudge_count(fm, period, recipient):
    """Bump the per-period nudge counter on `fm` in place (append-only per period).

    fm["nudge_counts"][period][recipient] += 1, plus a `response_rate` stub the UI
    reads (acked/sent; acked starts at 0 -- a later task wires acknowledgement).
    Mutating the passed fm is deliberate: reconcile_program persists this same fm
    dict in its single _write_program_file, so the counter rides that one write
    (no second file write). Idempotent within a period only insofar as each fired
    nudge increments once -- a suppressed nudge never reaches here.
    """
    counts = fm.setdefault("nudge_counts", {})
    period_counts = counts.setdefault(period, {})
    period_counts[recipient] = period_counts.get(recipient, 0) + 1
    sent = sum(v for pc in counts.values() for v in pc.values())
    rr = fm.setdefault("response_rate", {})
    if isinstance(rr, dict):
        rr["sent"] = sent
        rr.setdefault("acked", 0)


def _open_agent_task_types(task_lib, program_id):
    """Return the set of task_types carried by OPEN agent-queue tasks tagged with
    `program_id` (the produce-artifact dedupe fence).

    Mirrors _open_propose_update_ops but for the agent queue: collect the
    `task_type` of every open agent card tagged with this program_id, so a second
    reconcile in the same period cannot dispatch a duplicate worker run while one
    is still open. list_tasks already projects `task_type`, so no re-read needed.
    """
    types = set()
    for t in task_lib.list_tasks(queue="agent", status="open"):
        if program_id not in (t.get("tags") or []):
            continue
        tt = t.get("task_type")
        if tt:
            types.add(tt)
    return types


def _dispatch_agent_task(task_id):
    """Fire task_dispatch.py --task {task_id} in the background (detached).

    Mirrors cron_lib._auto_dispatch: spawn the dispatcher as a detached process
    with the Claude-stripped headless env and the platform process-group kwargs.
    Kept as a module-level function so tests can monkeypatch it (no real claude
    spawn under test). Never raises - a failed spawn is logged to stderr.
    """
    scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dispatch_script = os.path.join(scripts_dir, "task_dispatch.py")
    env = platform_lib.headless_claude_env()
    try:
        subprocess.Popen(
            [sys.executable, dispatch_script, "--task", task_id],
            cwd=os.path.dirname(scripts_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **platform_lib.process_group_kwargs(),
        )
    except Exception as e:
        sys.stderr.write(f"[cadence] Failed to dispatch agent task {task_id}: {e}\n")


def _propose_phase_advance(fm, type_entry, body):
    """The INTERPRETATION door's gate. Returns an advance-phase mutation or None.

    Returns {"op": "advance-phase", "to": <next>, "checkpoint": <cp_id>,
    "from": <current>} only when ALL hold:
      - the current phase has an `exit_checkpoint`, is not terminal, and that
        checkpoint exists in fm["checkpoints"];
      - that checkpoint's instrument is NOT mechanical (human-attested / unclear -
        the mechanical case is Task 5's fact door);
      - a fresh INTERPRETIVE `completion` observation is present: a kind=completion
        observation whose `source` does NOT start with `adapter:` (interpretive =
        movement-watch, not the tracker) and dated on/after the current phase's
        `phase_entered` (you cannot complete a phase's exit before entering it);
      - there is a real next phase.
    Else None. Proposal only - never mutates the program (Task 7's accept applies
    it). ASCII-safe; tolerant of missing shapes (never raises).
    """
    phase = fm.get("phase")
    phases = type_entry.get("phases") or []
    phase_def = next(
        (p for p in phases if isinstance(p, dict) and p.get("id") == phase), {}
    )
    if phase_def.get("terminal"):
        return None
    cp_id = phase_def.get("exit_checkpoint")
    if not cp_id:
        return None

    cp = next(
        (c for c in (fm.get("checkpoints") or [])
         if isinstance(c, dict) and c.get("id") == cp_id),
        None,
    )
    if cp is None:
        return None

    # Mechanical instruments belong to the fact door, not the proposal door.
    if _instrument_is_mechanical(cp.get("instrument")):
        return None

    next_phase = _next_phase_id(type_entry, phase)
    if not next_phase:
        return None

    since = _phase_entered_date(fm, phase)
    since_iso = since.isoformat() if since else None
    if not _has_interpretive_completion(body, since=since_iso):
        return None

    return {
        "op": "advance-phase",
        "to": next_phase,
        "checkpoint": cp_id,
        "from": phase,
    }


def _has_interpretive_completion(body, since=None):
    """True when the body carries a FRESH, INTERPRETIVE `completion` observation.

    Interpretive = a kind=completion observation whose `source` does NOT start
    with `adapter:` (the tracker grounding shape). Such an observation is a
    movement-watch read of a meeting/thread, not a deterministic tracker fact -
    enough to PROPOSE (not auto-apply) a human-attested phase advance. When
    `since` (an ISO date) is given, the observation must be dated on/after it -
    the current phase's entry date. Tolerant: an unparseable body yields no match.
    """
    for date_str, kind, source, _claim in _iter_observations(body):
        if kind != "completion" or source.startswith("adapter:"):
            continue
        if since and (not date_str or date_str < since):
            continue
        return True
    return False


def _build_birth_description(proposal, program_id):
    """Build a <=2-sentence ASCII birth-proposal card body (invariant #8).

    Renders the prefilled-program preview a human sees when accepting a birth:
    the target type, the proposed title, and the citations that earned it (so the
    diff and its evidence are both on the card). `program_id` is the intake
    register backlink. ASCII only, no em-dash; tolerant of missing fields.
    """
    program_type = proposal.get("program_type", "?")
    title = proposal.get("title", "?")
    citations = proposal.get("citations") or []
    cites = ", ".join(str(c) for c in citations) if citations else "none"
    return (
        f"Cadence proposes birthing a {program_type} program '{title}' from "
        f"intake {program_id}. Earned by: {cites}."
    )


def _build_proposal_description(mutation, body, program_id):
    """Build a <=2-sentence ASCII proposal card body (invariant #8).

    States the proposed change (advance from -> to on which checkpoint) and the
    interpretive observation's claim that earned it, so the human accepting the
    card sees both the diff and its citation. ASCII arrows, no em-dash. A birth
    proposal (op "birth") has no from/to/checkpoint shape -- it is delegated to
    _build_birth_description, which renders the prefilled-program preview.
    """
    if isinstance(mutation, dict) and mutation.get("op") == "birth":
        return _build_birth_description(mutation, program_id)
    frm = mutation.get("from", "?")
    to = mutation.get("to", "?")
    cp = mutation.get("checkpoint", "?")
    claim = _latest_interpretive_claim(body) or "a phase-complete signal"
    return (
        f"Cadence proposes advancing {program_id}: phase {frm} -> {to} "
        f"on checkpoint {cp}. Signal: {claim}"
    )


def _latest_interpretive_claim(body):
    """Return the last interpretive completion observation's claim, or None.

    Interpretive = kind=completion with a non-adapter source (movement-watch's
    read of a meeting/thread). Used only to cite the proposal in the card body.
    """
    claim = None
    for _date, kind, source, c in _iter_observations(body):
        if kind == "completion" and not source.startswith("adapter:"):
            claim = c
    return claim


def _build_archive_description(mutation, program_id):
    """Build a one-line description for an archive proposal card.

    Format: reason + citations + program backlink.
    """
    reason = mutation.get("reason", "unknown")
    citations = mutation.get("citations", [])
    cite_str = "; ".join(citations) if citations else "(no citations)"

    # Backlink to the program
    program_link = f"[{program_id}](/programs/{program_id})"

    return f"{reason} ({cite_str}) - {program_link}"


def _propose_archive(fm, type_entry, body):
    """Propose archive mutation if ANY fact indicates completion.

    Facts checked:
    1. Program phase is terminal
    2. A "did-it-work" checkpoint is verified/met
    3. A completion observation cites a tracker as closed

    Returns:
      - A dict with op:"archive", reason, citations if archive is proposed
      - None otherwise
    """
    # Fact 1: Terminal phase
    phase = fm.get("phase")
    if phase and program_lib._terminal_phase(type_entry, phase):
        return {
            "op": "archive",
            "reason": f"reached terminal phase: {phase}",
            "citations": [phase]  # cite the phase name
        }

    # Fact 2: did-it-work checkpoint verified
    checkpoints = fm.get("checkpoints") or []
    for cp in checkpoints:
        if "did-it-work" in (cp.get("id", "") + cp.get("kind", "")):
            if cp.get("status") in {"verified", "met"}:
                return {
                    "op": "archive",
                    "reason": f"did-it-work verified: {cp.get('id', 'unnamed')}",
                    "citations": [cp.get("id", "unknown")]
                }

    # Fact 3: Tracker-closed observation
    # Scan body for ## Observations section, look for a completion obs whose source cites a tracker
    # and claim mentions closed
    if body:
        lines = body.split("\n")
        in_obs = False
        for i, line in enumerate(lines):
            if line.startswith("## Observations"):
                in_obs = True
                continue
            if in_obs and line.startswith("## "):
                break  # end of observations
            if in_obs and line.startswith("- ") and "completion" in line.lower() and "closed" in line.lower():
                # Simple check: if line mentions completion and closed, it's a tracker-closed completion
                # Extract the source if possible (e.g., "source: gong:call-123")
                # For now, just cite "tracker-closed"
                return {
                    "op": "archive",
                    "reason": "tracker-closed (completion observation)",
                    "citations": ["tracker-closed"]
                }

    return None


def _evaluate_emitters(program, type_entry, verdict, facts, body=None, root=None,
                       period=None, registry=None):
    """Evaluate the type's declarative emitters. Returns created task ids.

    Three emitter families fire here, all Tier-1 (LOCAL cards, no external writes,
    no judge/ladder):

      - `escalate` (on `drift:<verdict>`): dedupe against open human cards already
        tagged with this program_id; if none, create one high-priority human card
        tagged [program_id, "cadence"].
      - `propose-update` (on `phase-advance-proposable`): the interpretation door.
        The `on` string is just the trigger NAME; the real gate is
        `_propose_phase_advance`, which returns an advance-phase mutation only when
        a human-attested exit checkpoint has a fresh interpretive completion
        observation. When it fires, create a `recommendation` card
        (task_type=cadence-propose-update) carrying the mutation as `proposal`,
        tagged [program_id, "cadence"]. Deduped against any OPEN propose-update
        card already carrying this program_id AND the same op.
      - `produce-artifact` (on `cycle-fresh`): the worker-dispatch door. This
        function only runs on a fresh cycle, so the trigger name simply marks the
        emitter as fresh-cycle scoped. Creates an AGENT-queue task for the named
        `worker` (the worker name doubles as task_type, e.g. `priority-digest`),
        tagged [program_id, "cadence"], then dispatches it via
        `_dispatch_agent_task`. Deduped against any OPEN agent task tagged with
        this program_id already carrying that task_type (once per period).

    Any other recognized action no-ops this increment (logged to stderr).
    """
    emitters = type_entry.get("emitters") or []
    if not emitters:
        return []

    registry = registry or {}
    fm = program["frontmatter"]
    program_id = fm.get("program_id")
    title = fm.get("title") or program_id or "Program"
    emitted = []
    open_tags = None        # lazily computed on the first escalate fire
    open_prop_ops = None    # lazily computed on the first propose-update fire
    open_agent_types = None  # lazily computed on the first produce-artifact fire
    open_birth_ids = None   # lazily computed on the first candidate-ripe fire

    for em in emitters:
        action = em.get("action")
        on = em.get("on")

        if action == "escalate":
            if on != f"drift:{verdict}":
                continue
            # Lazy import on the emitting path only - the pure-verdict path never
            # imports task_lib. Imported once here, not again per card.
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

        elif action == "propose-update" and on == "candidate-ripe":
            # The BIRTH door (inc4a): the program-intake register's emitter. Each
            # OPEN candidate that crosses its TARGET type's birth_threshold becomes
            # a birth proposal -- mirror the advance-phase propose-update card, but
            # dedupe by candidate_id (every birth shares op "birth", so op-dedupe
            # would collapse them). The intake program_id tags every card.
            births = _propose_births(fm, registry, body or "")
            if not births:
                continue
            import task_lib
            if open_birth_ids is None:
                open_birth_ids = _open_birth_candidate_ids(task_lib, program_id)
            for birth in births:
                cid = birth.get("candidate_id")
                if cid in open_birth_ids:
                    continue  # an open birth proposal already covers this candidate
                task_id, _ = task_lib.create_task(
                    title=f"Birth {birth['program_type']}: {birth['title']}?",
                    queue="human",
                    priority="high",
                    creator="cadence",
                    card_type="recommendation",
                    task_type="cadence-propose-update",
                    tags=[program_id, "cadence"],
                    proposal=birth,
                    description=_build_birth_description(birth, program_id),
                )
                emitted.append(task_id)
                if cid:
                    open_birth_ids.add(cid)

        elif action == "propose-update" and on == "completion-verified":
            # The ARCHIVE door (inc4b): propose archiving when facts indicate completion.
            # The mutation function is the real gate: _propose_archive checks terminal
            # phase, verified checkpoints, and tracker-closed observations.
            mutation = _propose_archive(fm, type_entry, body or "")
            if not mutation:
                continue
            import task_lib
            if open_prop_ops is None:
                open_prop_ops = _open_propose_update_ops(task_lib, program_id)
            if mutation["op"] in open_prop_ops:
                continue  # an open proposal for the same op already exists -> dedupe
            task_id, _ = task_lib.create_task(
                title=f"{title}: archive?",
                queue="human",
                priority="high",
                creator="cadence",
                card_type="recommendation",
                task_type="cadence-propose-update",
                tags=[program_id, "cadence"],
                proposal=mutation,
                description=_build_archive_description(mutation, program_id),
            )
            emitted.append(task_id)
            open_prop_ops.add(mutation["op"])

        elif action == "propose-update":
            # The mutation function is the real gate (the `on` string is only a
            # trigger name). Only advance-phase proposals are produced in 3a.
            mutation = _propose_phase_advance(fm, type_entry, body or "")
            if not mutation:
                continue
            import task_lib
            if open_prop_ops is None:
                open_prop_ops = _open_propose_update_ops(task_lib, program_id)
            if mutation["op"] in open_prop_ops:
                continue  # an open proposal for the same op already exists -> dedupe
            task_id, _ = task_lib.create_task(
                title=f"{title}: advance to {mutation['to']}?",
                queue="human",
                priority="high",
                creator="cadence",
                card_type="recommendation",
                task_type="cadence-propose-update",
                tags=[program_id, "cadence"],
                proposal=mutation,
                description=_build_proposal_description(mutation, body or "", program_id),
            )
            emitted.append(task_id)
            open_prop_ops.add(mutation["op"])

        elif action == "produce-artifact":
            # Fresh-cycle scoped: this function only runs on a fresh cycle, so the
            # trigger name marks the emitter as fresh-cycle. The worker name
            # doubles as the agent task_type.
            if on != "cycle-fresh":
                continue
            worker = em.get("worker")
            if not worker:
                continue
            import task_lib
            if open_agent_types is None:
                open_agent_types = _open_agent_task_types(task_lib, program_id)
            if worker in open_agent_types:
                continue  # an open run for this worker already exists -> dedupe
            task_id, _ = task_lib.create_task(
                title=f"{title}: draft {period or 'this'} digest",
                queue="agent",
                task_type=worker,
                creator="cadence",
                tags=[program_id, "cadence"],
                description=(
                    f"Draft the weekly priorities digest for {program_id} "
                    f"from the portfolio."
                ),
            )
            emitted.append(task_id)
            # Reflect the just-created task so a second produce-artifact emitter
            # in the same evaluation cannot double-fire for this worker.
            open_agent_types.add(worker)
            _dispatch_agent_task(task_id)

        elif action == "draft-message":
            # The rate-capped nudge primitive. Fresh-cycle scoped like
            # produce-artifact (this function only runs on a fresh cycle). Resolve
            # the recipient profile-driven (role-based, never a literal), enforce
            # max_nudges_per_person_per_week per recipient, and record a per-period
            # response-rate counter on the in-memory fm (reconcile_program persists
            # it in its single write). A capped recipient is SUPPRESSED: no card,
            # and a suppression marker rides the cycle log's `emitted:` clause.
            if on and on != "cycle-fresh":
                continue
            import task_lib
            channel, recipient = _resolve_nudge_target(fm, root=root)
            cap = em.get("max_nudges_per_person_per_week")
            if cap is not None:
                try:
                    cap = int(cap)
                except (TypeError, ValueError):
                    cap = None
            if cap is not None:
                # Enforce the cap off the PERIOD-KEYED counter on the program
                # frontmatter, not a cross-period open-card scan. In this system
                # messaging is normally unconfigured, so a created send-message
                # card never sends and stays `open` indefinitely; an open-card
                # scan would let the first period's nudge suppress every later
                # period's. nudge_counts is period-scoped, so the cap is correctly
                # "N per recipient per period".
                already = (fm.get("nudge_counts") or {}).get(period, {}).get(recipient, 0)
                if already >= cap:
                    # Suppress: surface it in the cycle log (no card created).
                    emitted.append(f"nudge suppressed (cap {cap}/wk)")
                    continue
            task_id, _ = task_lib.create_task(
                title=f"{title}: nudge {recipient}",
                queue="collab",
                creator="cadence",
                task_type="send-message",
                tags=[program_id, "cadence"],
                description=_build_nudge_description(facts, program_id, recipient),
                message_channel=channel,
                message_to=recipient,
                # The shipper builds the outgoing draft from message_body, so the
                # nudge text must ride that field too (description is the
                # human-facing card body; duplicating the text is fine).
                message_body=_build_nudge_description(facts, program_id, recipient),
            )
            emitted.append(task_id)
            _record_nudge_count(fm, period, recipient)

        elif action:
            sys.stderr.write(
                f"[cadence] emitter action '{action}' not acted on this "
                f"increment ({program_id})\n"
            )

    return emitted


def _has_adapter_completion(body, anchor=None, since=None):
    """True when the body carries a RELEVANT, FRESH adapter `completion`.

    A match requires a kind=`completion` observation whose `source` starts with
    `adapter:` (the tracker-truth grounding shape, e.g.
    `adapter:project_management:EPIC-204`) AND, when scoping args are given:
      - `anchor`: the source must reference THIS program's tracker anchor, so an
        unrelated adapter completion can never advance this program; and
      - `since` (an ISO date): the observation must be dated on/after it -- the
        current phase's entry date. You cannot complete a phase's exit before
        entering the phase, so a stale completion recorded in an earlier phase
        must not flip a later phase's checkpoint.
    Such an observation is the deterministic signal the tracker confirms done --
    enough to flip a still-pending MECHANICAL checkpoint to met and advance.
    Tolerant: an unparseable body simply yields no match (never raises).
    """
    for date, kind, source, _claim in _iter_observations(body):
        if kind != "completion" or not source.startswith("adapter:"):
            continue
        if anchor and anchor not in source:
            continue
        if since and (not date or date < since):
            continue
        return True
    return False


def _iter_observations(body):
    """Yield (date, kind, source, claim) for each observation in `body`.

    A thin adapter over program_lib.iter_observations (the canonical, source-
    exposing reader that now lives in the lower layer for DRY - program_lib never
    imports reconcile, so the reader had to move down, not up). reconcile's
    callers only need the 4-tuple, so this drops the `sentinel` field that the
    program_lib reader also yields. Never raises.
    """
    for date, kind, _sentinel, source, claim in program_lib.iter_observations(body):
        yield (date, kind, source, claim)


# _next_phase_id moved to program_lib (the lower layer) so the fact door and the
# proposal applier share ONE next-phase lookup. reconcile calls program_lib's.
_next_phase_id = program_lib._next_phase_id


def _maybe_advance_phase(fm, type_entry, body, now):
    """The FACT door: auto-advance a pipeline phase on a mechanically-confirmed
    exit checkpoint. Returns (body, advanced_record | None).

    Eligibility (all must hold):
      - the current phase has an `exit_checkpoint` in the type and is not terminal,
      - that checkpoint exists in `fm["checkpoints"]`,
      - its `instrument` is mechanical (_instrument_is_mechanical),
      - AND it is already `status == met` OR a fresh adapter-grounded `completion`
        observation is present (the tracker confirms done).

    On advance it mutates `fm` in place (flips the checkpoint to met if needed;
    sets `phase` to the next phase; stamps `phase_entered` for the new phase to
    today, respecting the existing dict-vs-scalar form) and returns
    {"from", "to", "checkpoint"}. Human-attested / unclear instruments, terminal
    phases, and unconfirmed checkpoints return (body, None) -- Task 6 will propose
    those. Idempotent: after advancing, the new phase's own exit_checkpoint is
    still pending, so it cannot chain-advance in the same tick.
    """
    phase = fm.get("phase")
    phases = type_entry.get("phases") or []
    phase_def = next(
        (p for p in phases if isinstance(p, dict) and p.get("id") == phase), {}
    )
    if phase_def.get("terminal"):
        return body, None
    cp_id = phase_def.get("exit_checkpoint")
    if not cp_id:
        return body, None

    checkpoints = fm.get("checkpoints") or []
    cp = next(
        (c for c in checkpoints if isinstance(c, dict) and c.get("id") == cp_id),
        None,
    )
    if cp is None:
        return body, None

    if not _instrument_is_mechanical(cp.get("instrument")):
        return body, None  # human-attested / unclear -> Task 6's proposal door

    # An adapter completion only counts when it cites THIS program's tracker anchor
    # and post-dates entry into the current phase (you cannot complete a phase's
    # exit before entering it) -- so a stale or unrelated completion never advances.
    anchor = program_lib.tracker_anchor(fm)
    since = _phase_entered_date(fm, phase)
    confirmed = cp.get("status") == "met" or _has_adapter_completion(
        body, anchor=anchor, since=since.isoformat() if since else None)
    if not confirmed:
        return body, None

    next_phase = _next_phase_id(type_entry, phase)
    if not next_phase:
        return body, None  # no successor (already the last/terminal phase)

    # The fact, grounded in the adapter observation: the checkpoint is met.
    if cp.get("status") != "met":
        cp["status"] = "met"

    # Advance through the SHARED stamp (program_lib._advance_phase_fm) so the
    # fact door and the proposal applier touch phase/phase_entered identically.
    today = _to_date(now).isoformat()
    program_lib._advance_phase_fm(fm, next_phase, today)

    return body, {"from": phase, "to": next_phase, "checkpoint": cp_id}


def reconcile_program(program, registry, now=None, force=False, root=None):
    """Run one program's reconcile cycle. Returns a result dict.

    `program` is the read_program shape ({"frontmatter", "body", "filepath"}).
    Computes the verdict, then guards to once-per-cadence-period: if this
    program already ran this period (and not `force`), returns without touching
    the file. On a fresh cycle, evaluates the type's declarative emitters
    (the escalate card fires here, deduped), runs the FACT-door phase advancement
    (_maybe_advance_phase), writes drift/last_cycle/last_run + any phase change
    back into the frontmatter, and appends a `## Cycles` log entry (recording any
    emitted card ids and any advancement) via _write_program_file. When a phase
    advanced, a second write follows: program_lib.append_observation stamps the
    source-cited `completion` fact observation (it re-reads + rewrites the file,
    so it must run AFTER the frontmatter write).

    Returns {"program_id", "verdict", "new_cycle": bool, "emitted": [ids],
    "advanced": {from, to, checkpoint} | None}.
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
    emitted = _evaluate_emitters(
        program, type_entry, verdict, facts, body=body, root=root, period=period,
        registry=registry
    )

    # The FACT door: mutate `fm` in place (phase + checkpoint) when the current
    # phase's mechanical exit checkpoint is confirmed done. The mutation and its
    # cycle note land in the SINGLE _write_program_file below; the grounded fact
    # OBSERVATION is stamped AFTER that write (append_observation re-reads +
    # rewrites the file, so it would otherwise lose this frontmatter change).
    advanced = None
    if type_entry.get("state_model") == "pipeline":
        body, advanced = _maybe_advance_phase(fm, type_entry, body, now)

    fm["drift"] = verdict
    fm["last_cycle"] = period
    fm["last_run"] = program_lib._now_iso()
    body = _append_cycle_entry(
        body, period, verdict, facts, emitted=emitted, advanced=advanced
    )

    filepath = program.get("filepath")
    if not filepath:
        filepath = os.path.join(
            program_lib._program_dir(root), f"{fm['program_id']}.md"
        )
    # WRITE 1: phase/checkpoint mutation + verdict + cycle note, in one write.
    program_lib._write_program_file(filepath, fm, body)

    # WRITE 2 (only when we advanced): the source-cited fact observation. This is
    # a separate call deliberately - append_observation re-reads the just-written
    # file (now carrying the new phase) and appends under ## Observations,
    # deduped. Ordering it AFTER write 1 keeps the phase change durable; doing it
    # before would be clobbered by write 1's frontmatter serialization.
    if advanced:
        # append_observation reconstructs the file path from (program_id, root)
        # via program_lib._program_dir(root) = <root>/datasets/programs. When the
        # caller passed no `root` but the program carries an absolute filepath
        # (the test + reconcile_all shape), derive the matching root from that
        # path so the observation lands on the SAME file we just wrote, never the
        # real datasets/ dir.
        obs_root = root
        if obs_root is None:
            obs_root = os.path.dirname(os.path.dirname(os.path.dirname(filepath)))
        program_lib.append_observation(
            fm["program_id"],
            kind="completion",
            sentinel="reconciler",
            source=f"checkpoint:{advanced['checkpoint']}",
            claim=(
                f"Phase advanced {advanced['from']} -> {advanced['to']} "
                f"on checkpoint {advanced['checkpoint']}."
            ),
            date=_to_date(now).isoformat(),
            root=obs_root,
        )

    return {
        "program_id": fm.get("program_id"),
        "verdict": verdict,
        "new_cycle": True,
        "emitted": emitted,
        "advanced": advanced,
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
        # list_programs sets a top-level program_id with a filename fallback, so a
        # malformed program still names its file in the error line (not a bare "?").
        program_id = program.get("program_id") or "?"
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
