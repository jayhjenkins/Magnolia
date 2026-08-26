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
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import harness_lib
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
_ARCHIVE_COOLDOWN_DAYS = 7 # don't propose archive within this many days of terminal phase entry
_TRACKER_STALE_DAYS = 3    # tracker observations older than this are unreliable


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

_INACTIVE_STATUSES = frozenset({
    "backlog", "next", "not started", "to do", "open", "new",
})


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
    daily -> ISO date `YYYY-MM-DD`.
    monthly -> `YYYY-MM`.
    `now` may be a date or datetime.
    """
    if cadence == "daily":
        return _to_date(now).isoformat()
    if cadence == "monthly":
        d = _to_date(now)
        return f"{d.year}-{d.month:02d}"
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

    # Severity-driven registers (the portfolio-health janitor): when items carry an
    # explicit `severity` (holding/drifting/broken finding), the worst severity IS
    # the verdict -- the age/policy math does not apply to health findings. Normal
    # registers (no severity) fall through to the age window below.
    severities = [it.get("severity") for it in items
                  if isinstance(it, dict) and it.get("severity") in _VERDICTS]
    if severities:
        worst = "holding"
        for s in severities:
            worst = _worse(worst, s)
        worst_item = next(
            (it for it in items if it.get("severity") == worst), None)
        reason = (worst_item or {}).get("name", "findings")
        return worst, {"reason": reason, "next": "review findings"}

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
    for t in task_lib.list_tasks(queue=None, status="open"):
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


def _resolved_propose_update_ops(task_lib, program_id):
    """Return {op: resolution_date} for recently resolved cadence-propose-update
    cards tagged with program_id (the resolution-aware half of the dedupe fence).

    Scans the archive for cancelled AND completed proposals. For each match,
    records the resolution date (the ``updated`` field truncated to YYYY-MM-DD).
    If the same op was resolved multiple times, keeps the latest date. The
    caller compares this date against the latest observation date in the program
    body: if no new observations arrived after the resolution, the proposal is
    suppressed.
    """
    resolved = {}
    for t in task_lib.list_archived(limit=200):
        if t.get("status") not in ("cancelled", "done"):
            continue
        if t.get("task_type") != "cadence-propose-update":
            continue
        try:
            full = task_lib.read_task(t["id"])
        except Exception:
            continue
        fm = full.get("frontmatter") or {}
        if program_id not in (fm.get("tags") or []):
            continue
        proposal = fm.get("proposal") or {}
        op = proposal.get("op") if isinstance(proposal, dict) else None
        if not op:
            continue
        res_date = (fm.get("updated") or fm.get("created") or "")[:10]
        if op not in resolved or res_date > resolved[op]:
            resolved[op] = res_date
    return resolved


def _suppressed_by_resolution(op, resolved_prop_ops, body):
    """Return True if ``op`` was resolved (cancelled or completed) and no new observations arrived since."""
    rej_date_str = resolved_prop_ops.get(op)
    if not rej_date_str:
        return False
    latest_obs = _latest_observation_date(body)
    if not latest_obs:
        return True  # no observations at all -> suppress
    rej_date = _parse_iso_date(rej_date_str)
    if rej_date is None:
        return False  # unparseable rejection date -> don't suppress
    return latest_obs <= rej_date


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
    for t in task_lib.list_tasks(queue=None, status="open"):
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


_LLM_EVAL_TIMEOUT = 180
_LLM_EVAL_TIER = "deep"


def _llm_evaluate_proposal(program_title, current_phase, target_phase,
                            phase_description, observations,
                            frontmatter=None, body=None):
    """Ask Claude whether evidence supports advancing to the target phase.

    Returns (approved, reason). Fail-closed: returns (False, ...) on any
    dispatch failure so bad proposals don't reach the board.
    """
    obs_text = "\n".join(f"- {o}" for o in observations[-15:]) if observations else "(none)"
    context_parts = []
    if frontmatter:
        drift = frontmatter.get("drift", "")
        if drift:
            context_parts.append(f"Current drift status: {drift}")
        checkpoints = frontmatter.get("checkpoints") or []
        if checkpoints:
            cp_lines = []
            for cp in checkpoints:
                label = cp.get("label", cp.get("id", "?"))
                status = cp.get("status", "?")
                due = cp.get("due", "")
                cp_line = f"  - {label}: {status}"
                if due:
                    cp_line += f" (due {due})"
                cp_lines.append(cp_line)
            context_parts.append("Checkpoints:\n" + "\n".join(cp_lines))
    if body:
        cycle_lines = _extract_recent_cycles(body, max_cycles=4)
        if cycle_lines:
            context_parts.append("Recent cycle verdicts:\n" + "\n".join(
                f"  - {c}" for c in cycle_lines))
    context_section = ""
    if context_parts:
        context_section = "\nProgram health:\n" + "\n".join(context_parts) + "\n"
    intent_section = ""
    if body:
        raw_intent = program_lib._parse_intent(body)
        if raw_intent:
            intent_section = f"\nSuccess criteria / Intent:\n{raw_intent}\n"
    prompt = (
        "You are evaluating whether a product initiative should advance to "
        "the next phase. You must weigh ALL the evidence -- including "
        "negative signals (risks, blockers, overdue checkpoints, broken "
        "drift) -- not just the latest positive observations. Pay close "
        "attention to the success criteria / intent: if KRs or metric "
        "targets are not met, the program is NOT ready to advance.\n\n"
        f"Program: {program_title}\n"
        f"Current phase: {current_phase}\n"
        f"Proposed phase: {target_phase}\n"
        f"What '{target_phase}' means: {phase_description}\n"
        f"{intent_section}"
        f"{context_section}\n"
        f"Evidence (oldest to newest):\n{obs_text}\n\n"
        "Based on ALL the evidence above -- including success criteria, "
        "health status, checkpoint progress, and cycle history -- does the "
        f"program meet the criteria for '{target_phase}'?\n"
        "Reply with exactly YES or NO on the first line, "
        "then a one-sentence reason on the second line."
    )
    import profile_lib
    model = profile_lib.resolve_model(_LLM_EVAL_TIER)
    cmd, harness_name = harness_lib.build_oneshot_cmd(prompt, model)
    env = platform_lib.headless_harness_env(harness_name)
    try:
        proc = subprocess.run(
            cmd, cwd=os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))),
            env=env, capture_output=True, text=True,
            timeout=_LLM_EVAL_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"[cadence] LLM eval failed ({exc.__class__.__name__}), fail-closed\n")
        return False, "evaluation unavailable -- fail-closed"
    if proc.returncode != 0:
        sys.stderr.write(f"[cadence] LLM eval exited {proc.returncode}, fail-closed\n")
        return False, "evaluation unavailable -- fail-closed"
    out = harness_lib.unwrap_oneshot_result(proc.stdout, harness_name)
    first_line = (out or "").strip().split("\n")[0].strip().upper()
    reason = "\n".join((out or "").strip().split("\n")[1:]).strip() or "no reason given"
    if first_line.startswith("NO"):
        return False, reason
    return True, reason


def _extract_recent_cycles(body, max_cycles=4):
    """Extract the most recent cycle verdicts from the ## Cycles section."""
    lines = []
    in_cycles = False
    for line in (body or "").split("\n"):
        if line.strip().startswith("## Cycles"):
            in_cycles = True
            continue
        if in_cycles:
            if line.strip().startswith("## ") and not line.strip().startswith("## Cycles"):
                break
            stripped = line.strip()
            if stripped.startswith("### "):
                lines.append(stripped[4:].strip())
    return lines[-max_cycles:] if lines else []


_LLM_EVAL_KINDS = frozenset({
    "status-signal", "completion", "commitment", "risk", "blocker",
})


def _llm_evaluate_tracker_proposal(program_title, tracker_key, current_status,
                                    evidence_claims):
    """Ask Claude whether evidence represents genuine active work vs incidental.

    Returns (approved, reason). Fail-closed: returns (False, ...) on any
    dispatch failure.
    """
    claims_text = "\n".join(f"- {c}" for c in evidence_claims[-5:]) if evidence_claims else "(none)"
    prompt = (
        "You are evaluating whether evidence from meetings and documents "
        "represents genuine active work on a product initiative, or just "
        "incidental mentions (sharing for awareness, referencing in passing, "
        "discussing without doing, etc.).\n\n"
        f"Program: {program_title}\n"
        f"Tracker: {tracker_key}\n"
        f"Tracker status: '{current_status}' (considered inactive)\n\n"
        f"Evidence claims:\n{claims_text}\n\n"
        "Does this evidence represent genuine active work that contradicts "
        "the tracker status? Reply with exactly YES or NO on the first line, "
        "then a one-sentence reason on the second line."
    )
    import profile_lib
    model = profile_lib.resolve_model(_LLM_EVAL_TIER)
    cmd, harness_name = harness_lib.build_oneshot_cmd(prompt, model)
    env = platform_lib.headless_harness_env(harness_name)
    try:
        proc = subprocess.run(
            cmd, cwd=os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))),
            env=env, capture_output=True, text=True,
            timeout=_LLM_EVAL_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"[cadence] LLM tracker eval failed ({exc.__class__.__name__}), fail-closed\n")
        return False, "evaluation unavailable -- fail-closed"
    if proc.returncode != 0:
        sys.stderr.write(f"[cadence] LLM tracker eval exited {proc.returncode}, fail-closed\n")
        return False, "evaluation unavailable -- fail-closed"
    out = harness_lib.unwrap_oneshot_result(proc.stdout, harness_name)
    first_line = (out or "").strip().split("\n")[0].strip().upper()
    reason = "\n".join((out or "").strip().split("\n")[1:]).strip() or "no reason given"
    if first_line.startswith("NO"):
        return False, reason
    return True, reason


def _llm_evaluate_archive_proposal(program_title, archive_reason, citations,
                                    observations):
    """Ask Claude whether a program is genuinely ready to archive.

    Returns (approved, reason). Fail-closed: returns (False, ...) on any
    dispatch failure.
    """
    obs_text = "\n".join(f"- {o}" for o in observations[-5:]) if observations else "(none)"
    cite_text = ", ".join(str(c) for c in citations) if citations else "(none)"
    prompt = (
        "You are evaluating whether a product initiative has genuinely "
        "completed and is ready to archive.\n\n"
        f"Program: {program_title}\n"
        f"Reason for archive: {archive_reason}\n"
        f"Evidence: {cite_text}\n\n"
        f"Recent observations:\n{obs_text}\n\n"
        "Is this program genuinely complete and ready to archive, or might "
        "the completion signal be premature? Reply with exactly YES or NO "
        "on the first line, then a one-sentence reason on the second line."
    )
    import profile_lib
    model = profile_lib.resolve_model(_LLM_EVAL_TIER)
    cmd, harness_name = harness_lib.build_oneshot_cmd(prompt, model)
    env = platform_lib.headless_harness_env(harness_name)
    try:
        proc = subprocess.run(
            cmd, cwd=os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))),
            env=env, capture_output=True, text=True,
            timeout=_LLM_EVAL_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"[cadence] LLM archive eval failed ({exc.__class__.__name__}), fail-closed\n")
        return False, "evaluation unavailable -- fail-closed"
    if proc.returncode != 0:
        sys.stderr.write(f"[cadence] LLM archive eval exited {proc.returncode}, fail-closed\n")
        return False, "evaluation unavailable -- fail-closed"
    out = harness_lib.unwrap_oneshot_result(proc.stdout, harness_name)
    first_line = (out or "").strip().split("\n")[0].strip().upper()
    reason = "\n".join((out or "").strip().split("\n")[1:]).strip() or "no reason given"
    if first_line.startswith("NO"):
        return False, reason
    return True, reason


def _llm_evaluate_date_proposal(program_title, current_phase, field,
                                 jira_date, observations, phase_description=""):
    """Ask Claude whether a Jira date is realistic given the program's state.

    Returns (approved, reason). approved=True means the date IS unrealistic and
    the update proposal should fire. Fail-closed: returns (False, ...) on any
    dispatch failure so questionable dates don't get flagged without evidence.
    """
    obs_text = "\n".join(f"- {o}" for o in observations[-15:]) if observations else "(none)"
    field_label = field.replace("_", " ").upper()
    phase_ctx = f"\nWhat '{current_phase}' means: {phase_description}" if phase_description else ""
    prompt = (
        "You are evaluating whether a product initiative's Jira date is "
        "realistic given its current state. Consider ALL the evidence -- "
        "recent progress, blockers, deployment status, and the overall "
        "trajectory -- not just the phase label.\n\n"
        f"Program: {program_title}\n"
        f"Current phase: {current_phase}{phase_ctx}\n"
        f"Jira {field_label}: {jira_date}\n\n"
        f"Recent observations (oldest to newest):\n{obs_text}\n\n"
        f"Based on the evidence, is the {field_label} of {jira_date} "
        f"unrealistic and should be updated? Consider whether the program's "
        f"actual progress supports hitting this date.\n"
        "Reply with exactly YES (date is unrealistic) or NO (date is "
        "achievable) on the first line, then a one-sentence reason on the "
        "second line."
    )
    import profile_lib
    model = profile_lib.resolve_model(_LLM_EVAL_TIER)
    cmd, harness_name = harness_lib.build_oneshot_cmd(prompt, model)
    env = platform_lib.headless_harness_env(harness_name)
    try:
        proc = subprocess.run(
            cmd, cwd=os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__)))),
            env=env, capture_output=True, text=True,
            timeout=_LLM_EVAL_TIMEOUT,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        sys.stderr.write(f"[cadence] LLM date eval failed ({exc.__class__.__name__}), fail-closed\n")
        return False, "evaluation unavailable -- fail-closed"
    if proc.returncode != 0:
        sys.stderr.write(f"[cadence] LLM date eval exited {proc.returncode}, fail-closed\n")
        return False, "evaluation unavailable -- fail-closed"
    out = harness_lib.unwrap_oneshot_result(proc.stdout, harness_name)
    first_line = (out or "").strip().split("\n")[0].strip().upper()
    reason = "\n".join((out or "").strip().split("\n")[1:]).strip() or "no reason given"
    if first_line.startswith("NO"):
        return False, reason
    return True, reason


def _gather_observation_claims(body):
    """Extract observation claims from a program body for LLM evaluation."""
    claims = []
    for _date, kind, source, claim in _iter_observations(body or ""):
        if kind in _LLM_EVAL_KINDS and not source.startswith("adapter:"):
            claims.append(claim)
    return claims


def _propose_phase_advance(fm, type_entry, body):
    """The INTERPRETATION door's gate. Returns an advance-phase mutation or None.

    Returns {"op": "advance-phase", "to": <next>, "checkpoint": <cp_id|None>,
    "from": <current>} when:
      - the current phase is not terminal, and there is a next phase;
      - a fresh INTERPRETIVE phase-evidence observation is present
        (kind in _PHASE_EVIDENCE_KINDS, source NOT from `adapter:`,
        dated on/after phase_entered);
      - AND one of:
        (a) the phase has an exit_checkpoint whose matching program checkpoint is
            non-mechanical (the original gate), OR
        (b) the phase has no exit_checkpoint or the program has no matching
            checkpoint object -- the evidence-only path for types like eos-rock
            whose checkpoints are program-specific milestones, not phase gates.

    The fact door (mechanical adapter completion) is unaffected: it still requires
    both a checkpoint and a mechanical instrument. This door only PROPOSES (creates
    a card for human approval), never auto-advances.
    """
    phase = fm.get("phase")
    phases = type_entry.get("phases") or []
    phase_def = next(
        (p for p in phases if isinstance(p, dict) and p.get("id") == phase), {}
    )
    if phase_def.get("terminal"):
        return None

    next_phase = _next_phase_id(type_entry, phase)
    if not next_phase:
        return None

    cp_id = phase_def.get("exit_checkpoint")
    if cp_id:
        cp = next(
            (c for c in (fm.get("checkpoints") or [])
             if isinstance(c, dict) and c.get("id") == cp_id),
            None,
        )
        if cp is not None and _instrument_is_mechanical(cp.get("instrument")):
            return None

    since = _phase_entered_date(fm, phase)
    since_iso = since.isoformat() if since else None
    if not _has_phase_evidence(body, since=since_iso):
        return None

    return {
        "op": "advance-phase",
        "to": next_phase,
        "checkpoint": cp_id,
        "from": phase,
    }


_PHASE_EVIDENCE_KINDS = frozenset({"status-signal", "completion", "commitment"})

_PHASE_ADVANCE_KINDS = frozenset({"completion"})


def _has_phase_evidence(body, since=None):
    """True when the body carries a FRESH, INTERPRETIVE observation strong enough
    to earn a phase-advance proposal.

    Only `completion` and `commitment` observations qualify. `status-signal`
    (activity was mentioned) is NOT sufficient -- it records that work is
    happening, not that a phase transition occurred. The LLM eval gate downstream
    still sees all observation kinds as context, but the proposal gate here
    requires a stronger signal. Adapter observations (the fact door's domain) are
    excluded. When `since` (an ISO date) is given, the observation must be dated
    on/after it. Tolerant: an unparseable body yields no match.
    """
    for date_str, kind, source, _claim in _iter_observations(body):
        if kind not in _PHASE_ADVANCE_KINDS or source.startswith("adapter:"):
            continue
        if since and (not date_str or date_str < since):
            continue
        return True
    return False


def _propose_tracker_update(fm, type_entry, body, now=None):
    """Detect tracker-status-mismatch: Jira reports inactive but evidence says active.

    Returns {"op": "update-tracker", "tracker_key": key, "current_status": status,
    "evidence_claims": [claims]} or None.
    """
    anchor = program_lib.tracker_anchor(fm)
    if not anchor:
        return None

    tracker_status = None
    tracker_date = None
    for date_str, kind, source, claim in _iter_observations(body):
        if not source.startswith("adapter:project_management:"):
            continue
        if kind != "status-signal":
            continue
        m = re.search(r"Tracker reports status '([^']+)'", claim)
        if m:
            tracker_status = m.group(1)
            tracker_date = date_str

    if not tracker_status:
        return None
    if tracker_status.lower() not in _INACTIVE_STATUSES:
        return None

    if tracker_date and now is not None:
        try:
            tracker_obs_date = date.fromisoformat(tracker_date)
            if (_to_date(now) - tracker_obs_date).days > _TRACKER_STALE_DAYS:
                sys.stderr.write(
                    f"[cadence] Tracker data stale ({tracker_date}), "
                    f"skipping mismatch proposal\n")
                return None
        except (ValueError, TypeError):
            pass

    cutoff = None
    if tracker_date:
        try:
            cutoff = (date.fromisoformat(tracker_date) - timedelta(days=14)).isoformat()
        except (ValueError, TypeError):
            cutoff = None

    evidence_claims = []
    for date_str, kind, source, claim in _iter_observations(body):
        if source.startswith("adapter:"):
            continue
        if kind not in _PHASE_EVIDENCE_KINDS:
            continue
        if cutoff and (not date_str or date_str < cutoff):
            continue
        evidence_claims.append(claim)

    if not evidence_claims:
        return None

    return {
        "op": "update-tracker",
        "tracker_key": anchor,
        "current_status": tracker_status,
        "tracker_observed": tracker_date,
        "evidence_claims": evidence_claims[:3],
    }


def _build_tracker_update_description(mutation, program_id):
    """Build a clear tracker-update proposal card body."""
    key = mutation.get("tracker_key", "?")
    status = mutation.get("current_status", "?")
    observed = mutation.get("tracker_observed", "?")
    claims = mutation.get("evidence_claims", [])
    cite = claims[0][:120] if claims else "activity evidence"
    return (
        f"Change Jira {key} status: currently '{status}' "
        f"(as of {observed}), evidence shows active work.\n"
        f"Signal: {cite}"
    )


_EA_CHECKPOINT_TOKENS = {"ship", "beta", "build-exit", "ea", "ftue-ea"}
_GA_CHECKPOINT_TOKENS = {"did-it-work", "verified", "ga", "activation"}
_DATE_DRIFT_OVERDUE_DAYS = 7


def _propose_date_update(fm, type_entry, body, now=None):
    """Detect date drift: checkpoint overdue but Jira date not updated.

    Returns {"op": "update-tracker-date", "tracker_key": key,
    "field": "ea_date"|"ga_date", "current_jira_date": ...,
    "checkpoint_id": ..., "checkpoint_label": ..., "overdue_days": int,
    "reason": ...} or None.
    """
    anchor = program_lib.tracker_anchor(fm)
    if not anchor:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    now_date = _to_date(now)

    # Parse latest Jira dates from date-change observations.
    jira_ea = None
    jira_ga = None
    for _date_str, kind, source, claim in _iter_observations(body or ""):
        if kind != "date-change" or not source.startswith("adapter:"):
            continue
        m_ea = re.match(r"EA date is (\S+)\.", claim)
        if m_ea:
            jira_ea = _parse_iso_date(m_ea.group(1))
        m_ga = re.match(r"GA date is (\S+)\.", claim)
        if m_ga:
            jira_ga = _parse_iso_date(m_ga.group(1))

    # Scan checkpoints for overdue ones that map to a Jira date field.
    for cp in fm.get("checkpoints") or []:
        if cp.get("status") in {"met", "missed", "verified"}:
            continue
        due = _parse_iso_date(cp.get("due"))
        if due is None:
            continue
        overdue_days = (now_date - due).days
        if overdue_days < _DATE_DRIFT_OVERDUE_DAYS:
            continue

        cp_id = cp.get("id", "")
        cp_label = cp.get("label", cp_id)
        # Determine which Jira field this checkpoint maps to.
        field = None
        jira_date = None
        if any(tok in cp_id for tok in _EA_CHECKPOINT_TOKENS):
            field = "ea_date"
            jira_date = jira_ea
        elif any(tok in cp_id for tok in _GA_CHECKPOINT_TOKENS):
            field = "ga_date"
            jira_date = jira_ga

        if not field:
            continue

        # If Jira date is already past the checkpoint due, the date hasn't
        # been pushed out to reflect the slip.
        if jira_date and jira_date > now_date:
            continue

        return {
            "op": "update-tracker-date",
            "tracker_key": anchor,
            "field": field,
            "current_jira_date": jira_date.isoformat() if jira_date else None,
            "checkpoint_id": cp_id,
            "checkpoint_label": cp_label,
            "overdue_days": overdue_days,
            "reason": (
                f"{cp_label} overdue by {overdue_days} days; "
                f"Jira {field.replace('_', ' ')} should be updated"
            ),
        }

    # Also check the program's top-level due date against the GA field.
    program_due = _parse_iso_date(fm.get("due"))
    if program_due and jira_ga:
        if program_due > jira_ga and jira_ga < now_date:
            return {
                "op": "update-tracker-date",
                "tracker_key": anchor,
                "field": "ga_date",
                "current_jira_date": jira_ga.isoformat(),
                "checkpoint_id": None,
                "checkpoint_label": "program due date",
                "overdue_days": (now_date - jira_ga).days,
                "reason": (
                    f"Jira GA date ({jira_ga.isoformat()}) has passed but "
                    f"program is due {program_due.isoformat()}"
                ),
            }

    # Phase-date coherence: detect when Jira EA/GA dates are unrealistic
    # given the program's current pipeline phase. Catches programs that have
    # no checkpoints (or none matching EA/GA tokens) but whose tracker dates
    # conflict with their phase position. Marked source:"phase-coherence" so
    # the emitter path can gate these through an LLM evaluation.
    phases = type_entry.get("phases") or []
    if phases and (jira_ea or jira_ga):
        current_phase = fm.get("phase")
        current_idx = next(
            (i for i, p in enumerate(phases)
             if isinstance(p, dict) and p.get("id") == current_phase),
            -1,
        )
        if current_idx >= 0:
            ea_phase_idx = None
            ga_phase_idx = None
            for i, ph in enumerate(phases):
                if not isinstance(ph, dict):
                    continue
                cp_id = ph.get("exit_checkpoint") or ""
                if any(tok in cp_id for tok in _EA_CHECKPOINT_TOKENS):
                    ea_phase_idx = i
                if any(tok in cp_id for tok in _GA_CHECKPOINT_TOKENS):
                    ga_phase_idx = i
            if ga_phase_idx is None:
                for i in range(len(phases) - 1, -1, -1):
                    if isinstance(phases[i], dict) and not phases[i].get("terminal"):
                        ga_phase_idx = i
                        break

            if jira_ea and ea_phase_idx is not None and current_idx <= ea_phase_idx:
                overdue = (now_date - jira_ea).days
                if overdue >= _DATE_DRIFT_OVERDUE_DAYS:
                    return {
                        "op": "update-tracker-date",
                        "tracker_key": anchor,
                        "field": "ea_date",
                        "current_jira_date": jira_ea.isoformat(),
                        "checkpoint_id": None,
                        "checkpoint_label": f"phase still {current_phase}",
                        "overdue_days": overdue,
                        "source": "phase-coherence",
                        "reason": (
                            f"EA date ({jira_ea.isoformat()}) passed {overdue}d ago "
                            f"but program is still in {current_phase} phase"
                        ),
                    }

            if jira_ga and ga_phase_idx is not None and current_idx < ga_phase_idx:
                days_to_ga = (jira_ga - now_date).days
                if days_to_ga <= _SOON_WINDOW_DAYS:
                    overdue = max(0, -days_to_ga)
                    return {
                        "op": "update-tracker-date",
                        "tracker_key": anchor,
                        "field": "ga_date",
                        "current_jira_date": jira_ga.isoformat(),
                        "checkpoint_id": None,
                        "checkpoint_label": f"phase still {current_phase}",
                        "overdue_days": overdue,
                        "source": "phase-coherence",
                        "reason": (
                            f"GA date ({jira_ga.isoformat()}) "
                            + (f"in {days_to_ga}d" if days_to_ga > 0 else f"passed {-days_to_ga}d ago")
                            + f" but program is still in {current_phase} phase"
                        ),
                    }

    return None


def _build_date_update_description(mutation, program_id):
    """Build a date-update proposal card body."""
    key = mutation.get("tracker_key", "?")
    field = mutation.get("field", "?").replace("_", " ")
    current = mutation.get("current_jira_date", "not set")
    cp_label = mutation.get("checkpoint_label", "?")
    overdue = mutation.get("overdue_days", 0)
    return (
        f"Jira {key} {field} needs updating: {cp_label} is overdue by "
        f"{overdue} days. Current Jira {field}: {current}.\n"
        f"Review and update the date in Jira to reflect the actual timeline."
    )


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


def _build_proposal_description(mutation, body, program_id, type_entry=None):
    """Build a clear proposal card body showing what changes and why.

    Format: Change line, evidence, phase definition. ASCII only (invariant #8).
    """
    if isinstance(mutation, dict) and mutation.get("op") == "birth":
        return _build_birth_description(mutation, program_id)
    frm = mutation.get("from", "?")
    to = mutation.get("to", "?")
    claim = _latest_interpretive_claim(body) or "a phase-complete signal"
    if len(claim) > 120:
        claim = claim[:117] + "..."
    phase_desc = ""
    if type_entry:
        for ph in (type_entry.get("phases") or []):
            if ph.get("id") == to:
                phase_desc = ph.get("description", "")
                break
    lines = [f"Change {program_id} phase: {frm} -> {to}"]
    lines.append(f"Evidence: {claim}")
    if phase_desc:
        lines.append(f"What '{to}' means: {phase_desc}")
    return "\n".join(lines)


def _latest_interpretive_claim(body):
    """Return the last interpretive phase-evidence observation's claim, or None.

    Interpretive = kind in _PHASE_EVIDENCE_KINDS with a non-adapter source
    (movement-watch's read of a meeting/thread). Used to cite the triggering
    observation in the proposal card body.
    """
    claim = None
    for _date, kind, source, c in _iter_observations(body):
        if kind in _PHASE_EVIDENCE_KINDS and not source.startswith("adapter:"):
            claim = c
    return claim


def _build_archive_description(mutation, program_id):
    """Build a one-line description for an archive proposal card.

    Format: reason + citations + program backlink.
    """
    reason = mutation.get("reason", "unknown")
    citations = mutation.get("citations", [])
    cite_str = "; ".join(str(c) for c in citations) if citations else "none"
    # Plain-text backlink, matching _build_birth_description / _build_proposal_description
    # (no markdown link to a nonexistent route). ASCII only, no em-dash.
    return (
        f"Cadence proposes archiving {program_id}: {reason}. "
        f"Evidence: {cite_str}."
    )


def _propose_archive(fm, type_entry, body, now=None):
    """Propose archive mutation if ANY fact indicates completion.

    Facts checked:
    1. Program phase is terminal (with cool-down, pending-checkpoint guard,
       and future-due-date guard after entry)
    2. A "did-it-work" checkpoint is verified/met (strong signal, no guards)
    3. A completion observation cites a tracker as closed (strong signal)

    Returns:
      - A dict with op:"archive", reason, citations if archive is proposed
      - None otherwise
    """
    # Fact 1: Terminal phase
    phase = fm.get("phase")
    if phase and program_lib._terminal_phase(type_entry, phase):
        if now is not None:
            cooldown = type_entry.get(
                "archive_cooldown_days", _ARCHIVE_COOLDOWN_DAYS)
            entered = _phase_entered_date(fm, phase)
            if entered is not None:
                days_at_terminal = (_to_date(now) - entered).days
                if days_at_terminal < cooldown:
                    return None
            # Guard A: pending checkpoints block archive at terminal phase.
            _TERMINAL_CP_STATUSES = {"met", "missed", "verified"}
            for cp in fm.get("checkpoints") or []:
                if cp.get("status") not in _TERMINAL_CP_STATUSES:
                    return None
            # Guard B: future due date (>14 days out) blocks archive.
            due_raw = fm.get("due")
            if due_raw:
                due_date = _parse_iso_date(due_raw)
                if due_date and (due_date - _to_date(now)).days > 14:
                    return None
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

    # Fact 3: a `completion` observation reporting a tracker/epic closed.
    # Observations are "### <date> - sentinel:NAME [kind] (confidence X)" headers
    # followed by the claim text on the next line(s) -- NOT "- " bullets. Scan for
    # a [completion] header whose following claim mentions "closed", and cite the
    # observation's sentinel/source from the header.
    if body:
        in_obs = False
        current_hdr = None
        for line in body.split("\n"):
            s = line.strip()
            if s.startswith("## Observations"):
                in_obs = True
                continue
            if in_obs and s.startswith("## "):
                break  # end of the observations section
            if not in_obs:
                continue
            if s.startswith("### "):
                current_hdr = s
                continue
            if current_hdr and "[completion]" in current_hdr and "closed" in s.lower():
                return {
                    "op": "archive",
                    "reason": "tracker reported closed (completion observation)",
                    "citations": [_obs_source_from_header(current_hdr)],
                }

    return None


def _obs_source_from_header(header):
    """Extract a citation from an observation header line.

    Returns the `sentinel:NAME` token when present (e.g. "sentinel:tracker-truth"),
    else a generic "tracker" citation. ASCII only.
    """
    m = re.search(r"sentinel:([\w-]+)", header or "")
    return f"sentinel:{m.group(1)}" if m else "tracker"


# Days per cadence period -- the silent-archive threshold is N of these.
_PERIOD_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 30,
                "quarterly": 90}


def _period_days(type_entry):
    """Length of one cadence period in days for a type (defaults to weekly=7)."""
    cadence = (type_entry or {}).get("cadence") or "weekly"
    return _PERIOD_DAYS.get(cadence, 7)


def _latest_observation_date(body):
    """Most recent observation date in a program body, as a date, or None."""
    latest = None
    for obs_date, _kind, _source, _claim in _iter_observations(body or ""):
        parsed = _parse_iso_date(obs_date)
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return latest


def _propose_archive_silent(fm, type_entry, body, telemetry, now_iso):
    """Propose archive if the program has been silent too long AND the sentinel is live.

    Silent is defined by the type's `archive_after_silent_cycles` (in weeks): if the
    latest observation is older than (cycles * 7) days AND the movement-watch sentinel
    is live (has a recent last_run with no error), propose archive.

    Args:
      fm: program frontmatter dict
      type_entry: the type dict containing optional `archive_after_silent_cycles`
      body: program body (contains ## Observations)
      telemetry: sentinel telemetry dict from sentinel_runner.read_sentinel_runs()
      now_iso: ISO datetime string (e.g., from date.today().isoformat() or NOW.isoformat())

    Returns:
      - A dict with op:"archive", reason, citations if silent archive is proposed
      - None otherwise
    """
    silent_cycles = type_entry.get("archive_after_silent_cycles")
    if not silent_cycles:
        return None  # No silent policy configured for this type

    # Latest observation date = the program's last activity signal.
    latest_obs_date = _latest_observation_date(body)

    if not latest_obs_date:
        return None  # No observations, cannot apply silent archive

    # Threshold = the type's cadence-period length * N cycles (period-aware per the
    # approved design; defaults to weekly when a type declares no cadence).
    # now_iso may be a full datetime isoformat (the scheduler) OR a date string
    # (tests); _parse_iso_date accepts only YYYY-MM-DD, so take the date part.
    now = _parse_iso_date(str(now_iso)[:10])
    if now is None:
        return None  # unparseable clock -> cannot judge silence (never crash)
    silent_threshold_days = silent_cycles * _period_days(type_entry)
    days_silent = (now - latest_obs_date).days
    if days_silent < silent_threshold_days:
        return None  # Still active (within the threshold)

    # Check sentinel health: movement-watch must be LIVE (recent run, no error)
    if not telemetry:
        return None  # No telemetry, cannot verify sentinel is live
    movement_watch = telemetry.get("movement-watch", {})
    last_error = movement_watch.get("last_error")
    if last_error:
        return None  # Sentinel is blind (has error), suppress archive

    last_run_str = movement_watch.get("last_run")
    if not last_run_str:
        return None  # No last_run recorded, sentinel never ran

    # Check if sentinel last_run is recent enough (within last 7 days is reasonable)
    try:
        last_run = _parse_iso_date(last_run_str)
        sentinel_staleness = (now - last_run).days
        if sentinel_staleness > 7:
            return None  # Sentinel is blind (stale run > 7 days)
    except (ValueError, TypeError):
        return None  # Cannot parse last_run

    # All gates passed: propose archive
    return {
        "op": "archive",
        "reason": f"dormant {days_silent} days (threshold {silent_threshold_days})",
        "citations": ["silent-too-long"],
    }


def _active_family_count(root=None, exclude_family=None):
    """Distinct families with >=1 active program (the portfolio-rollup >=N gate).

    Counted from the program store, never a family literal: each active program's
    family is looked up from the registry by its type. `exclude_family` drops one
    family from the tally - the rollup passes its OWN (system) family so the
    seeded system programs (intake, janitor, the rollup itself) never count toward
    the "the operator runs >=2 families" threshold. A program of an unknown type
    is skipped. Never raises."""
    import program_lib
    try:
        reg = program_lib.load_registry(root)
    except Exception:
        return 0
    fam_by_type = {t.get("id"): t.get("family") for t in (reg.get("types") or [])}
    fams = set()
    for prog in program_lib.list_programs(status="active", root=root):
        fm = prog.get("frontmatter") or {}
        fam = fam_by_type.get(fm.get("type"))
        if fam and fam != exclude_family:
            fams.add(fam)
    return len(fams)


def _evaluate_emitters(program, type_entry, verdict, facts, body=None, root=None,
                       period=None, registry=None, now=None, weekday_only=False,
                       proposals_only=False):
    """Evaluate the type's declarative emitters. Returns created task ids.

    Three emitter families fire here, all Tier-1 (LOCAL cards, no external writes,
    no judge/ladder):

      - `escalate` (on `drift:<verdict>`): dedupe against open human cards already
        tagged with this program_id; if none, create one high-priority human card
        tagged [program_id, "cadence"].
      - `propose-update` (on `phase-advance-proposable`): the interpretation door.
        The `on` string is just the trigger NAME; the real gate is
        `_propose_phase_advance`, which returns an advance-phase mutation only when
        fresh phase evidence is present (status-signal, completion, or commitment
        from a non-adapter source). When it fires, create a `recommendation` card
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

    When `weekday_only` is True, only weekday-gated emitters are evaluated (used
    for mid-cycle weekday fire-ups).

    When `proposals_only` is True, only `propose-update` emitters are evaluated
    (escalate, produce-artifact, draft-message etc. are skipped). Used by the
    mid-cycle tick to evaluate proposal gates on every run without re-firing
    other emitters.

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
    resolved_prop_ops = None  # lazily computed: {op: rejection_date} from archive
    open_agent_types = None  # lazily computed on the first produce-artifact fire
    open_birth_ids = None   # lazily computed on the first candidate-ripe fire
    # Default now to today if not provided (for testing and background runs)
    if now is None:
        now = datetime.now(timezone.utc)
    now_iso = now.isoformat() if isinstance(now, datetime) else str(now)

    for em in emitters:
        action = em.get("action")
        on = em.get("on")

        fire_weekday = em.get("fire_weekday")
        if weekday_only and fire_weekday is None:
            continue
        if fire_weekday is not None:
            try:
                target = int(fire_weekday)
            except (TypeError, ValueError):
                target = None
            if target is not None and now.isoweekday() != target:
                continue

        fire_occurrence = em.get("fire_month_occurrence")
        if fire_occurrence is not None:
            try:
                target_occ = int(fire_occurrence)
            except (TypeError, ValueError):
                target_occ = None
            if target_occ is not None:
                d = _to_date(now)
                occurrence = (d.day - 1) // 7 + 1
                if occurrence != target_occ:
                    continue

        if proposals_only and action != "propose-update":
            continue

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
            mutation = _propose_archive(fm, type_entry, body or "", now=now)
            if not mutation:
                continue
            import task_lib
            if open_prop_ops is None:
                open_prop_ops = _open_propose_update_ops(task_lib, program_id)
            if mutation["op"] in open_prop_ops:
                continue  # an open proposal for the same op already exists -> dedupe
            if resolved_prop_ops is None:
                resolved_prop_ops = _resolved_propose_update_ops(task_lib, program_id)
            if _suppressed_by_resolution(mutation["op"], resolved_prop_ops, body):
                continue
            obs_claims = _gather_observation_claims(body)
            approved, reason = _llm_evaluate_archive_proposal(
                title, mutation.get("reason", ""), mutation.get("citations", []),
                obs_claims)
            if not approved:
                sys.stderr.write(
                    f"[cadence] LLM rejected archive {program_id}: {reason}\n")
                continue
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

        elif action == "propose-update" and on == "silent-too-long":
            # The SILENT ARCHIVE door (inc4b): propose archiving when the program
            # has been dormant longer than the type's archive_after_silent_cycles
            # AND the movement-watch sentinel is live (recent, no errors).
            # The mutation function is the real gate: _propose_archive_silent checks
            # observation staleness and sentinel health.
            import sentinel_runner
            telemetry = sentinel_runner.read_sentinel_runs(root)
            mutation = _propose_archive_silent(fm, type_entry, body or "", telemetry, now_iso)
            if not mutation:
                continue
            import task_lib
            if open_prop_ops is None:
                open_prop_ops = _open_propose_update_ops(task_lib, program_id)
            if mutation["op"] in open_prop_ops:
                continue  # an open proposal for the same op already exists -> dedupe
            if resolved_prop_ops is None:
                resolved_prop_ops = _resolved_propose_update_ops(task_lib, program_id)
            if _suppressed_by_resolution(mutation["op"], resolved_prop_ops, body):
                continue
            obs_claims = _gather_observation_claims(body)
            approved, reason = _llm_evaluate_archive_proposal(
                title, mutation.get("reason", ""), mutation.get("citations", []),
                obs_claims)
            if not approved:
                sys.stderr.write(
                    f"[cadence] LLM rejected silent archive {program_id}: {reason}\n")
                continue
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

        elif action == "propose-update" and on == "tracker-status-mismatch":
            mutation = _propose_tracker_update(fm, type_entry, body or "", now=now)
            if not mutation:
                continue
            import task_lib
            if open_prop_ops is None:
                open_prop_ops = _open_propose_update_ops(task_lib, program_id)
            if mutation["op"] in open_prop_ops:
                continue
            if resolved_prop_ops is None:
                resolved_prop_ops = _resolved_propose_update_ops(task_lib, program_id)
            if _suppressed_by_resolution(mutation["op"], resolved_prop_ops, body):
                continue
            obs_claims = mutation.get("evidence_claims", [])
            approved, reason = _llm_evaluate_tracker_proposal(
                title, mutation["tracker_key"], mutation.get("current_status", "?"),
                obs_claims)
            if not approved:
                sys.stderr.write(
                    f"[cadence] LLM rejected tracker-mismatch {program_id} "
                    f"{mutation['tracker_key']}: {reason}\n")
                continue
            task_id, _ = task_lib.create_task(
                title=f"{title}: update {mutation['tracker_key']} from '{mutation.get('current_status', '?')}'?",
                queue="human",
                priority="high",
                creator="cadence",
                card_type="recommendation",
                task_type="cadence-propose-update",
                tags=[program_id, "cadence"],
                proposal=mutation,
                description=_build_tracker_update_description(mutation, program_id),
            )
            emitted.append(task_id)
            open_prop_ops.add(mutation["op"])

        elif action == "propose-update" and on == "date-drift":
            mutation = _propose_date_update(fm, type_entry, body or "", now=now)
            if not mutation:
                continue
            import task_lib
            if open_prop_ops is None:
                open_prop_ops = _open_propose_update_ops(task_lib, program_id)
            if mutation["op"] in open_prop_ops:
                continue
            if resolved_prop_ops is None:
                resolved_prop_ops = _resolved_propose_update_ops(task_lib, program_id)
            if _suppressed_by_resolution(mutation["op"], resolved_prop_ops, body):
                continue
            if mutation.get("source") == "phase-coherence":
                phase_desc = ""
                for ph in (type_entry.get("phases") or []):
                    if ph.get("id") == fm.get("phase"):
                        phase_desc = ph.get("description", "")
                        break
                obs_claims = _gather_observation_claims(body)
                approved, reason = _llm_evaluate_date_proposal(
                    title, fm.get("phase", "?"),
                    mutation.get("field", "date"),
                    mutation.get("current_jira_date", "?"),
                    obs_claims, phase_description=phase_desc)
                if not approved:
                    sys.stderr.write(
                        f"[cadence] LLM says date achievable {program_id} "
                        f"{mutation.get('field')}: {reason}\n")
                    continue
            task_id, _ = task_lib.create_task(
                title=f"{title}: update Jira {mutation.get('field', 'date').replace('_', ' ')}?",
                queue="human",
                priority="high",
                creator="cadence",
                card_type="recommendation",
                task_type="cadence-propose-update",
                tags=[program_id, "cadence"],
                proposal=mutation,
                description=_build_date_update_description(mutation, program_id),
            )
            emitted.append(task_id)
            open_prop_ops.add(mutation["op"])

        elif action == "propose-update":
            mutation = _propose_phase_advance(fm, type_entry, body or "")
            if not mutation:
                continue
            import task_lib
            if open_prop_ops is None:
                open_prop_ops = _open_propose_update_ops(task_lib, program_id)
            if mutation["op"] in open_prop_ops:
                continue  # an open proposal for the same op already exists -> dedupe
            if resolved_prop_ops is None:
                resolved_prop_ops = _resolved_propose_update_ops(task_lib, program_id)
            if _suppressed_by_resolution(mutation["op"], resolved_prop_ops, body):
                continue
            target_phase_id = mutation.get("to", "")
            phase_desc = ""
            for ph in (type_entry.get("phases") or []):
                if ph.get("id") == target_phase_id:
                    phase_desc = ph.get("description", "")
                    break
            if phase_desc:
                obs_claims = _gather_observation_claims(body)
                approved, reason = _llm_evaluate_proposal(
                    title, fm.get("phase", "?"), target_phase_id,
                    phase_desc, obs_claims,
                    frontmatter=fm, body=body)
                if not approved:
                    sys.stderr.write(
                        f"[cadence] LLM rejected {program_id} "
                        f"{fm.get('phase')} -> {target_phase_id}: {reason}\n")
                    continue
            task_id, _ = task_lib.create_task(
                title=f"{title}: {mutation.get('from', '?')} -> {mutation['to']}?",
                queue="human",
                priority="high",
                creator="cadence",
                card_type="recommendation",
                task_type="cadence-propose-update",
                tags=[program_id, "cadence"],
                proposal=mutation,
                description=_build_proposal_description(
                    mutation, body or "", program_id, type_entry),
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
            # Optional declarative gate (the brief's ">=2 families" rule for the
            # cross-program rollup): fire only once the portfolio spans >= N active
            # families OTHER than this program's own. Below the threshold the
            # rollup stays inert (no empty digest). Counted from the store, never a
            # literal.
            min_families = em.get("min_active_families")
            if min_families is not None:
                try:
                    needed = int(min_families)
                except (TypeError, ValueError):
                    needed = 0
                if _active_family_count(
                        root, exclude_family=type_entry.get("family")) < needed:
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
                    f"Draft the {period or 'this period'} {worker} artifact for "
                    f"{program_id} from the portfolio."
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
            task_lib.update_task(task_id, changes={"agent_status": "complete"})
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


def _age_candidates(fm, now):
    """Derive each OPEN candidate's `age` (days since `opened`) in place.

    The register verdict (_verdict_register) ages an item by its numeric `age`
    field, but intake candidates carry `opened`, not `age` (4a M-3). Deriving it
    here lets the nursery drift on stale candidates and gives the janitor a real
    age to report. Only open/flagged candidates with a parseable `opened` are
    aged; closed-with-reason / birthed candidates are left untouched.
    """
    today = now.date() if isinstance(now, datetime) else _parse_iso_date(str(now))
    if today is None:
        return
    for it in fm.get("items") or []:
        if not isinstance(it, dict):
            continue
        if it.get("status", "open") not in {"open", "flagged"}:
            continue
        opened = it.get("opened")
        if not opened:
            continue
        parsed = _parse_iso_date(opened)
        if parsed is not None:
            it["age"] = (today - parsed).days


# How long a sentinel that HAS run may go silent before it is "blind" (stale).
_SENTINEL_STALE_DAYS = 14


def _scan_portfolio_health(root, now):
    """Scan the program store + intake register + telemetry, return findings.

    Each finding is a register item dict shaped for both the severity-aware
    register verdict and the UI: {name, owner, kind, severity, status, age?}.
    severity is one of holding/drifting/broken; a broken finding escalates via
    the type's `drift:broken` emitter. The janitor REPORTS -- it never archives
    (the per-program archive doors propose that). ASCII-safe (invariant #8).

    Findings:
      - blind-sentinel (broken): a sentinel that HAS run but is now errored or
        stale (> _SENTINEL_STALE_DAYS). A never-run sentinel is NOT flagged (that
        is a cold start, not a regression -- the dead-vs-blind distinction).
      - stale-active (drifting): an active program silent past its type's
        archive_after_silent_cycles.
      - aging-candidate (drifting): an open intake candidate older than the
        nursery policy.
      - duplicate (drifting): two active programs with the same normalized title.
      - supply (drifting): no active programs in the roadmap family (the team has
        nothing refined in front of it).
    """
    today = now.date() if isinstance(now, datetime) else _parse_iso_date(str(now))
    findings = []

    programs = program_lib.list_programs(status="active", root=root)
    reg = program_lib.load_registry()
    types_by_id = {t.get("id"): t for t in reg.get("types", [])}

    telemetry = {}
    try:
        import sentinel_runner
        telemetry = sentinel_runner.read_sentinel_runs(root) or {}
    except Exception:
        telemetry = {}

    # 1. Blind sentinels: ran before, now errored or stale.
    for name, entry in telemetry.items():
        if not isinstance(entry, dict):
            continue
        reason = None
        if entry.get("last_error"):
            reason = "error"
        else:
            last_run = _parse_iso_date(entry.get("last_run"))
            if last_run is None or (today and (today - last_run).days > _SENTINEL_STALE_DAYS):
                reason = "stale"
        if reason:
            findings.append({
                "name": f"sentinel {name} blind ({reason})", "owner": name,
                "kind": "blind-sentinel", "severity": "broken", "status": "open",
            })

    # 2. Stale actives + 4. duplicate detection (single pass over actives).
    norm_titles = {}
    for p in programs:
        fm = p["frontmatter"]
        pid = fm.get("program_id")
        te = types_by_id.get(fm.get("type"), {})
        n = te.get("archive_after_silent_cycles")
        if n:
            last_obs = _latest_observation_date(p["body"])
            if last_obs and today:
                days = (today - last_obs).days
                if days > n * _period_days(te):
                    findings.append({
                        "name": f"{fm.get('title', pid)} silent {days}d",
                        "owner": pid, "kind": "stale-active",
                        "severity": "drifting", "status": "open", "age": days,
                    })
        key = program_lib._norm_title_key(fm.get("title", ""))
        if key:
            norm_titles.setdefault(key, []).append(pid)

    for key, pids in norm_titles.items():
        if len(pids) > 1:
            findings.append({
                "name": f"possible duplicates: {', '.join(pids)}",
                "owner": pids[0], "kind": "duplicate",
                "severity": "drifting", "status": "open",
            })

    # 3. Aging candidates in the intake register(s).
    for p in programs:
        fm = p["frontmatter"]
        if fm.get("type") != "program-intake":
            continue
        try:
            policy = int(fm.get("policy", _DEFAULT_POLICY_DAYS))
        except (TypeError, ValueError):
            policy = _DEFAULT_POLICY_DAYS
        for it in fm.get("items") or []:
            if not isinstance(it, dict) or it.get("status") not in {"open", "flagged"}:
                continue
            opened = _parse_iso_date(it.get("opened"))
            if opened and today and (today - opened).days > policy:
                findings.append({
                    "name": f"candidate aging: {it.get('title', '?')}",
                    "owner": it.get("id"), "kind": "aging-candidate",
                    "severity": "drifting", "status": "open",
                    "age": (today - opened).days,
                })

    # 5. Supply: nothing refined in the roadmap family.
    roadmap_actives = [
        p for p in programs
        if (types_by_id.get(p["frontmatter"].get("type"), {}).get("family") == "roadmap")
    ]
    if not roadmap_actives:
        findings.append({
            "name": "no active roadmap programs (supply low)", "owner": "portfolio",
            "kind": "supply", "severity": "drifting", "status": "open",
        })

    return findings


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

    # The janitor refreshes its findings from a live portfolio scan each cycle,
    # BEFORE the verdict (the severity-aware register verdict reads them). Self-
    # hosting: the janitor is just a program whose items are computed, not declared.
    if fm.get("type") == "portfolio-health":
        fm["items"] = _scan_portfolio_health(root, now)

    # Age open intake candidates from their `opened` date so the register verdict
    # can drift on a stale nursery (4a M-3). No-op for items without `opened`.
    _age_candidates(fm, now)

    verdict, facts = compute_verdict(program, registry, now)

    cadence = _resolve_cadence(fm, registry)
    period = current_period(cadence, now)

    is_new_cycle = force or fm.get("last_cycle") != period

    # Resolve the type entry once (used by both fresh-cycle and mid-cycle paths).
    type_id = fm.get("type")
    type_entry = next(
        (t for t in registry.get("types", []) if t.get("id") == type_id), {}
    )

    # Check for pending weekday-gated emitters that haven't fired this period.
    # A weekday emitter whose target day is today (and correct month occurrence
    # if specified) and hasn't been recorded in weekday_fired for this period is
    # eligible for a mid-cycle fire.
    pending_weekday = False
    if not is_new_cycle:
        today_wd = now.isoweekday() if hasattr(now, "isoweekday") else None
        if today_wd is not None:
            fired_days = (fm.get("weekday_fired") or {}).get(period, [])
            today_d = _to_date(now)
            today_occ = (today_d.day - 1) // 7 + 1
            for em in (type_entry.get("emitters") or []):
                fw = em.get("fire_weekday")
                if fw is None:
                    continue
                try:
                    target = int(fw)
                except (TypeError, ValueError):
                    continue
                if target != today_wd:
                    continue
                fo = em.get("fire_month_occurrence")
                if fo is not None:
                    try:
                        target_occ = int(fo)
                    except (TypeError, ValueError):
                        target_occ = None
                    if target_occ is not None and target_occ != today_occ:
                        continue
                fired_key = target if fo is None else f"{target}:{fo}"
                if fired_key not in fired_days:
                    pending_weekday = True
                    break

    if not is_new_cycle and not pending_weekday:
        emitted = []
        if type_entry.get("state_model") == "pipeline":
            emitted = _evaluate_emitters(
                program, type_entry, verdict, facts, body=body, root=root,
                period=period, registry=registry, now=now, proposals_only=True,
            )
        drift_changed = fm.get("drift") != verdict
        if drift_changed or fm.get("last_run", "") < program_lib._now_iso()[:13] or emitted:
            fm["drift"] = verdict
            fm["last_run"] = program_lib._now_iso()
            filepath = program.get("filepath")
            if not filepath:
                filepath = os.path.join(
                    program_lib._program_dir(root), f"{fm['program_id']}.md"
                )
            program_lib._write_program_file(filepath, fm, body)
        return {
            "program_id": fm.get("program_id"),
            "verdict": verdict,
            "new_cycle": False,
            "emitted": emitted,
        }

    if not is_new_cycle and pending_weekday:
        # Mid-cycle weekday fire: evaluate only weekday-gated emitters.
        emitted = _evaluate_emitters(
            program, type_entry, verdict, facts, body=body, root=root,
            period=period, registry=registry, now=now, weekday_only=True
        )
        wf = dict(fm.get("weekday_fired") or {})
        fired_keys = list(wf.get(period, []))
        for em in (type_entry.get("emitters") or []):
            fw = em.get("fire_weekday")
            if fw is None:
                continue
            try:
                target = int(fw)
            except (TypeError, ValueError):
                continue
            if target == today_wd:
                fo = em.get("fire_month_occurrence")
                fired_keys.append(target if fo is None else f"{target}:{fo}")
        wf[period] = list(set(fired_keys))
        fm["weekday_fired"] = wf
        fm["drift"] = verdict
        fm["last_run"] = program_lib._now_iso()
        filepath = program.get("filepath")
        if not filepath:
            filepath = os.path.join(
                program_lib._program_dir(root), f"{fm['program_id']}.md"
            )
        program_lib._write_program_file(filepath, fm, body)
        return {
            "program_id": fm.get("program_id"),
            "verdict": verdict,
            "new_cycle": False,
            "emitted": emitted,
        }

    # Fresh cycle: evaluate emitters FIRST (so the cycle log can record them),
    # THEN write verdict back + append the cycle log, in ONE program-file write.
    emitted = _evaluate_emitters(
        program, type_entry, verdict, facts, body=body, root=root, period=period,
        registry=registry, now=now
    )

    # Record weekday firings for the fresh cycle so mid-cycle ticks don't re-fire.
    fresh_wf = {}
    today_wd = now.isoweekday() if hasattr(now, "isoweekday") else None
    if today_wd is not None:
        today_d = _to_date(now)
        today_occ = (today_d.day - 1) // 7 + 1
        for em in (type_entry.get("emitters") or []):
            fw = em.get("fire_weekday")
            if fw is None:
                continue
            try:
                target = int(fw)
            except (TypeError, ValueError):
                continue
            if target == today_wd:
                fo = em.get("fire_month_occurrence")
                if fo is not None:
                    try:
                        target_occ = int(fo)
                    except (TypeError, ValueError):
                        target_occ = None
                    if target_occ is not None and target_occ != today_occ:
                        continue
                fired_key = target if fo is None else f"{target}:{fo}"
                fresh_wf.setdefault(period, []).append(fired_key)
    fm["weekday_fired"] = fresh_wf

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
