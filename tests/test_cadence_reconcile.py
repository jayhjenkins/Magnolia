"""Tests for the pure verdict core of the Cadence reconciler (Task 1).

These cover ONLY the no-I/O helpers: current_period, _parse_iso_date, and
compute_verdict across all four state models. Program dicts are built inline in
the read_program shape ({"frontmatter": {...}, "body": ""}); the registry comes
from the real program_lib.load_registry().
"""

import json
import os
from datetime import date, datetime

import pytest

import program_lib as pl
import task_lib
from cadence import reconcile


@pytest.fixture(autouse=True)
def _isolated_task_queues(tmp_path_factory, monkeypatch):
    """Isolate task_lib for EVERY test so reconciling a broken program (which
    now fires the escalate emitter -> a human card) never touches the real
    datasets/tasks/. task_lib resolves queue dirs from module constants set at
    import, so we repoint both TASKS_DIR and COUNTER_FILE at a fresh tmp dir and
    create the four queue subdirs. Pure-verdict tests simply never exercise it.
    """
    tasks_dir = tmp_path_factory.mktemp("tasks")
    for q in ("human", "agent", "collab", "waiting"):
        (tasks_dir / q).mkdir(parents=True, exist_ok=True)
    counter = tasks_dir / "_counter"
    counter.write_text("1")
    archive = tasks_dir / "_archive"
    archive.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(task_lib, "COUNTER_FILE", str(counter))
    # ARCHIVE_DIR is computed at import from the ORIGINAL TASKS_DIR, so patch it
    # too -- otherwise any completed card would leak into the real archive.
    monkeypatch.setattr(task_lib, "ARCHIVE_DIR", str(archive))


# A fixed "now" used everywhere so verdicts are deterministic.
NOW = date(2026, 6, 16)


def _registry():
    return pl.load_registry()


def _prog(fm):
    return {"frontmatter": fm, "body": ""}


# ─── current_period ────────────────────────────────────────────────────────────

def test_current_period_weekly_format():
    # 2026-06-16 is a Tuesday in ISO week 25.
    assert reconcile.current_period("weekly", NOW) == "2026-W25"


def test_current_period_none_and_unknown_default_to_weekly():
    assert reconcile.current_period(None, NOW) == "2026-W25"
    assert reconcile.current_period("monthly", NOW) == "2026-W25"


def test_current_period_daily_is_iso_date():
    assert reconcile.current_period("daily", NOW) == "2026-06-16"


def test_current_period_accepts_datetime():
    dt = datetime(2026, 6, 16, 9, 30, 0)
    assert reconcile.current_period("weekly", dt) == "2026-W25"
    assert reconcile.current_period("daily", dt) == "2026-06-16"


# ─── _parse_iso_date ─────────────────────────────────────────────────────────

def test_parse_iso_date_accepts_iso_string():
    assert reconcile._parse_iso_date("2026-06-16") == date(2026, 6, 16)


def test_parse_iso_date_accepts_date():
    d = date(2026, 6, 16)
    assert reconcile._parse_iso_date(d) == d


def test_parse_iso_date_accepts_datetime():
    assert reconcile._parse_iso_date(datetime(2026, 6, 16, 9, 0)) == date(2026, 6, 16)


def test_parse_iso_date_rejects_human_strings():
    assert reconcile._parse_iso_date("Mon Jun 16") is None
    assert reconcile._parse_iso_date("Thu 9:00am") is None
    assert reconcile._parse_iso_date("Today") is None


def test_parse_iso_date_rejects_none_and_int():
    assert reconcile._parse_iso_date(None) is None
    assert reconcile._parse_iso_date(5) is None


# ─── pipeline ──────────────────────────────────────────────────────────────────

def test_pipeline_holding():
    # Checkpoint far in the future, current phase (execution) has no window.
    fm = {
        "type": "roadmap-initiative",
        "phase": "execution",
        "phase_entered": {"execution": "2026-06-01"},
        "checkpoints": [
            {"id": "ship", "due": "2026-09-15", "status": "pending"},
        ],
    }
    verdict, facts = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"
    assert isinstance(facts, dict)
    assert "reason" in facts and "next" in facts


def test_pipeline_drifting_checkpoint_due_soon():
    # Due in 5 days -> within the 7-day window -> drifting.
    fm = {
        "type": "roadmap-initiative",
        "phase": "execution",
        "phase_entered": {"execution": "2026-06-01"},
        "checkpoints": [
            {"id": "ship", "due": "2026-06-21", "status": "pending"},
        ],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "drifting"


def test_pipeline_broken_checkpoint_past_due():
    fm = {
        "type": "roadmap-initiative",
        "phase": "execution",
        "phase_entered": {"execution": "2026-06-01"},
        "checkpoints": [
            {"id": "ship", "due": "2026-06-06", "status": "pending"},
        ],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "broken"


def test_pipeline_broken_missed_status():
    fm = {
        "type": "roadmap-initiative",
        "phase": "execution",
        "phase_entered": {"execution": "2026-06-01"},
        "checkpoints": [
            {"id": "ship", "due": "2026-09-15", "status": "missed"},
        ],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "broken"


def test_pipeline_broken_phase_over_max_age():
    # discovery max_age_days = 21; entered 60 days before now -> broken.
    fm = {
        "type": "roadmap-initiative",
        "phase": "discovery",
        "phase_entered": {"discovery": "2026-04-17"},
        "checkpoints": [],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "broken"


def test_pipeline_drifting_phase_near_max_age():
    # planning max_age_days = 14; 0.8*14 = 11.2; entered 12 days before now.
    fm = {
        "type": "roadmap-initiative",
        "phase": "planning",
        "phase_entered": {"planning": "2026-06-04"},
        "checkpoints": [],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "drifting"


def test_pipeline_no_data_holding():
    fm = {
        "type": "roadmap-initiative",
        "phase": "execution",
        "checkpoints": [],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"


def test_pipeline_tolerates_scalar_phase_entered():
    # phase_entered as a scalar = the current phase's entered date.
    fm = {
        "type": "roadmap-initiative",
        "phase": "discovery",
        "phase_entered": "2026-04-17",
        "checkpoints": [],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "broken"


# ─── register ──────────────────────────────────────────────────────────────────

def test_register_holding():
    fm = {
        "type": "eos-issues",
        "policy": 21,
        "items": [{"name": "a", "owner": "ops", "age": 5}],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"


def test_register_drifting():
    # 0.8 * 21 = 16.8; age 18 -> drifting.
    fm = {
        "type": "eos-issues",
        "policy": 21,
        "items": [{"name": "a", "owner": "ops", "age": 18}],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "drifting"


def test_register_broken():
    fm = {
        "type": "eos-issues",
        "policy": 21,
        "items": [{"name": "a", "owner": "ops", "age": 25}],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "broken"


def test_register_no_items_holding():
    fm = {"type": "eos-issues", "policy": 21, "items": []}
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"


def test_register_default_policy():
    # No policy declared -> default 14; age 20 > 14 -> broken.
    fm = {"type": "eos-issues", "items": [{"name": "a", "age": 20}]}
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "broken"


# ─── target ────────────────────────────────────────────────────────────────────

def test_target_holding():
    fm = {
        "type": "did-it-work",
        "metric": {"actual": 58, "target": 55, "unit": "%"},
        "series": {"pred": [20, 30, 40, 48, 55], "act": [18, 32, 42, 52, 58]},
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"


def test_target_drifting():
    # tolerance default 8; expected = pred[4] = 55, actual = 45; diff 10 > 8.
    fm = {
        "type": "did-it-work",
        "series": {"pred": [20, 30, 40, 48, 55], "act": [18, 32, 42, 52, 45]},
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "drifting"


def test_target_broken():
    # diff = |55 - 30| = 25 > 2*8 = 16 -> broken.
    fm = {
        "type": "did-it-work",
        "series": {"pred": [20, 30, 40, 48, 55], "act": [18, 32, 42, 52, 30]},
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "broken"


def test_target_custom_tolerance():
    # tolerance 2; diff |55-58| = 3 > 2 but <= 4 -> drifting.
    fm = {
        "type": "did-it-work",
        "metric": {"tolerance": 2},
        "series": {"pred": [50, 55], "act": [49, 58]},
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "drifting"


def test_target_act_longer_than_pred_pins_to_last_prediction():
    # When actuals run past predictions, expected pins to the LAST prediction.
    # pred=[50,55], act=[49,58,99]: expected = pred[1] = 55, actual = 99;
    # diff |99-55| = 44 > 2*8 = 16 -> broken.
    fm = {
        "type": "did-it-work",
        "series": {"pred": [50, 55], "act": [49, 58, 99]},
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "broken"


def test_target_no_series_holding():
    fm = {"type": "did-it-work", "series": {}}
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"


def test_target_empty_act_holding():
    fm = {"type": "did-it-work", "series": {"pred": [1, 2], "act": []}}
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"


# ─── cycle ─────────────────────────────────────────────────────────────────────

def test_cycle_holding_latest_sent():
    fm = {
        "type": "weekly-priorities",
        "periods": [{"w": "W23", "s": "sent"}, {"w": "W24", "s": "sent"}],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"


def test_cycle_drifting_latest_late():
    fm = {
        "type": "weekly-priorities",
        "periods": [{"w": "W23", "s": "sent"}, {"w": "W24", "s": "late"}],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "drifting"


def test_cycle_broken_latest_missed():
    fm = {
        "type": "weekly-priorities",
        "periods": [{"w": "W23", "s": "sent"}, {"w": "W24", "s": "missed"}],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "broken"


def test_cycle_no_periods_holding():
    fm = {"type": "weekly-priorities", "periods": []}
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"


def test_pipeline_human_string_due_skipped_not_raised():
    # A PIPELINE program (roadmap-initiative) DOES read checkpoint `due` dates,
    # so this exercises the real reconcile continue-path on a non-ISO due: the
    # human-string checkpoint contributes no signal and must not raise. With no
    # other signal (current phase has no age window) the verdict is holding.
    fm = {
        "type": "roadmap-initiative",
        "phase": "execution",
        "phase_entered": {"execution": "2026-06-01"},
        "checkpoints": [
            {"id": "l10", "label": "L10", "due": "Thu 9:00am", "status": "pending"},
        ],
    }
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"


# ─── unknown / empty state model ────────────────────────────────────────────────

def test_unknown_type_holding():
    fm = {"type": "no-such-type"}
    verdict, _ = reconcile.compute_verdict(_prog(fm), _registry(), NOW)
    assert verdict == "holding"


def test_missing_type_holding():
    verdict, _ = reconcile.compute_verdict(_prog({}), _registry(), NOW)
    assert verdict == "holding"


# ─── reconcile_program (Task 2) ──────────────────────────────────────────────
#
# These exercise the stateful one-program cycle: compute verdict, guard to
# once-per-period, and on a fresh cycle write drift/last_cycle/last_run +
# append a `## Cycles` entry. Isolation is mandatory — every test seeds a tmp
# datasets root via program_lib.create_program(root=tmp); the real
# datasets/programs/ is NEVER touched. Emitters are Task 3 — emitted stays [].

PERIOD = "2026-W25"          # current_period("weekly", NOW)
OTHER_PERIOD = "2026-W24"    # a DIFFERENT period than NOW's


def _seed_broken_pipeline(root, last_cycle=OTHER_PERIOD):
    """Create a pipeline program that computes to `broken` (overdue checkpoint).

    Returns the program_id. The current phase (execution) carries no age window,
    so the only signal is the past-due pending checkpoint.
    """
    program_id, _ = pl.create_program(
        type="roadmap-initiative",
        title="Test initiative",
        owner_role="pm",
        frontmatter_extra={
            "phase": "execution",
            "phase_entered": {"execution": "2026-06-01"},
            "checkpoints": [
                {"id": "ship", "due": "2026-06-06", "status": "pending"},
            ],
            "last_cycle": last_cycle,
        },
        root=root,
    )
    return program_id


def test_reconcile_program_new_cycle_writes(tmp_path):
    root = str(tmp_path)
    program_id = _seed_broken_pipeline(root)
    program = pl.read_program(program_id, root=root)

    result = reconcile.reconcile_program(program, _registry(), now=NOW)

    assert result["program_id"] == program_id
    assert result["verdict"] == "broken"
    assert result["new_cycle"] is True
    # Task 3: a broken program fires the escalate emitter -> one human card.
    assert len(result["emitted"]) == 1

    reread = pl.read_program(program_id, root=root)
    fm = reread["frontmatter"]
    assert fm["drift"] == "broken"
    assert fm["last_cycle"] == reconcile.current_period("weekly", NOW)
    assert fm["last_cycle"] == PERIOD
    assert fm.get("last_run")  # ISO timestamp set
    # The body now carries a Cycles entry for this period + verdict.
    body = reread["body"]
    assert f"### {PERIOD} - broken" in body
    # Header sits inside the `## Cycles` section.
    assert "## Cycles" in body
    cycles_section = body.split("## Cycles", 1)[1]
    assert f"### {PERIOD} - broken" in cycles_section
    # The detail line is present and ASCII-clean.
    assert "checks:" in cycles_section
    assert "next:" in cycles_section


def test_reconcile_program_same_period_skips_emitters(tmp_path):
    root = str(tmp_path)
    program_id = _seed_broken_pipeline(root, last_cycle=PERIOD)
    program = pl.read_program(program_id, root=root)

    result = reconcile.reconcile_program(program, _registry(), now=NOW, force=False)

    assert result["new_cycle"] is False
    assert result["verdict"] == "broken"
    assert result["emitted"] == []


def test_mid_cycle_persists_drift_and_last_run(tmp_path):
    root = str(tmp_path)
    program_id = _seed_broken_pipeline(root, last_cycle=PERIOD)
    program = pl.read_program(program_id, root=root)
    old_fm = program["frontmatter"]
    old_drift = old_fm.get("drift")
    old_run = old_fm.get("last_run", "")

    result = reconcile.reconcile_program(program, _registry(), now=NOW, force=False)

    assert result["new_cycle"] is False
    reread = pl.read_program(program_id, root=root)
    assert reread["frontmatter"]["drift"] == "broken"
    assert reread["frontmatter"]["last_run"] >= old_run


def test_mid_cycle_does_not_append_cycle_entry(tmp_path):
    root = str(tmp_path)
    program_id = _seed_broken_pipeline(root, last_cycle=PERIOD)
    program = pl.read_program(program_id, root=root)
    body_before = program["body"]

    reconcile.reconcile_program(program, _registry(), now=NOW, force=False)

    reread = pl.read_program(program_id, root=root)
    cycle_entries_before = body_before.count("## Cycles")
    cycle_entries_after = reread["body"].count("## Cycles")
    assert cycle_entries_after == cycle_entries_before


def test_reconcile_program_force_reruns_same_period(tmp_path):
    root = str(tmp_path)
    program_id = _seed_broken_pipeline(root, last_cycle=PERIOD)
    program = pl.read_program(program_id, root=root)

    result = reconcile.reconcile_program(program, _registry(), now=NOW, force=True)

    assert result["new_cycle"] is True
    reread = pl.read_program(program_id, root=root)
    assert reread["frontmatter"]["last_cycle"] == PERIOD
    assert f"### {PERIOD} - broken" in reread["body"]


def test_reconcile_program_appends_without_losing_prior_entry(tmp_path):
    root = str(tmp_path)
    program_id = _seed_broken_pipeline(root)
    # Seed an existing Cycles entry, then reconcile a fresh cycle.
    program = pl.read_program(program_id, root=root)
    fm = program["frontmatter"]
    seeded_body = program["body"].replace(
        "## Cycles\n",
        "## Cycles\n\n### 2026-W24 - holding\nchecks: on track - emitted: none - next: none\n",
    )
    pl._write_program_file(program["filepath"], fm, seeded_body)

    program = pl.read_program(program_id, root=root)
    reconcile.reconcile_program(program, _registry(), now=NOW)

    body = pl.read_program(program_id, root=root)["body"]
    # Old entry survives, new entry appended.
    assert "### 2026-W24 - holding" in body
    assert f"### {PERIOD} - broken" in body
    # Order: prior entry precedes the new one (append-only).
    assert body.index("### 2026-W24 - holding") < body.index(f"### {PERIOD} - broken")


def test_reconcile_program_inserts_under_cycles_before_following_section(tmp_path):
    # A program body with a section AFTER `## Cycles` (a trailing `## Footnotes`).
    # The new entry must land UNDER `## Cycles` and BEFORE `## Footnotes`, and
    # the Footnotes content must be preserved verbatim.
    root = str(tmp_path)
    program_id = _seed_broken_pipeline(root)
    program = pl.read_program(program_id, root=root)
    fm = program["frontmatter"]
    # Append a Footnotes section after the seed's trailing `## Cycles`.
    seeded_body = program["body"].rstrip("\n") + "\n\n## Footnotes\n\nload-bearing footnote.\n"
    pl._write_program_file(program["filepath"], fm, seeded_body)

    program = pl.read_program(program_id, root=root)
    reconcile.reconcile_program(program, _registry(), now=NOW)

    body = pl.read_program(program_id, root=root)["body"]
    cycles_idx = body.index("## Cycles")
    entry_idx = body.index(f"### {PERIOD} - broken")
    footnotes_idx = body.index("## Footnotes")
    # New entry sits under `## Cycles` and before `## Footnotes`.
    assert cycles_idx < entry_idx < footnotes_idx
    # Footnotes content survives unchanged, after the heading.
    assert "## Footnotes\n\nload-bearing footnote." in body


def test_reconcile_program_cycle_header_is_ascii(tmp_path):
    root = str(tmp_path)
    program_id = _seed_broken_pipeline(root)
    program = pl.read_program(program_id, root=root)
    reconcile.reconcile_program(program, _registry(), now=NOW)

    body = pl.read_program(program_id, root=root)["body"]
    cycles_section = body.split("## Cycles", 1)[1]
    assert "—" not in cycles_section  # no em-dash
    assert "–" not in cycles_section  # no en-dash either


def test_reconcile_program_holding_writes_holding(tmp_path):
    root = str(tmp_path)
    # A register program within policy -> holding.
    program_id, _ = pl.create_program(
        type="eos-issues",
        title="Issues list",
        owner_role="ops",
        frontmatter_extra={
            "policy": 21,
            "items": [{"name": "a", "owner": "ops", "age": 3}],
            "last_cycle": OTHER_PERIOD,
        },
        root=root,
    )
    program = pl.read_program(program_id, root=root)

    result = reconcile.reconcile_program(program, _registry(), now=NOW)

    assert result["verdict"] == "holding"
    reread = pl.read_program(program_id, root=root)
    assert reread["frontmatter"]["drift"] == "holding"
    assert f"### {PERIOD} - holding" in reread["body"]


def test_reconcile_program_resolves_filepath_when_absent(tmp_path):
    # The read_program shape provides filepath; if a caller omits it we resolve
    # from program_id + root. Drop filepath to exercise that branch.
    root = str(tmp_path)
    program_id = _seed_broken_pipeline(root)
    program = pl.read_program(program_id, root=root)
    program.pop("filepath", None)

    result = reconcile.reconcile_program(program, _registry(), now=NOW, root=root)

    assert result["new_cycle"] is True
    assert f"### {PERIOD} - broken" in pl.read_program(program_id, root=root)["body"]


def test_reconcile_program_two_cycles_both_entries_survive(tmp_path):
    # Real two-cycle path (no string .replace): reconcile at an earlier period,
    # then a later one. BOTH `## Cycles` entries survive, in order.
    root = str(tmp_path)
    program_id = _seed_broken_pipeline(root, last_cycle="2026-W20")

    early = date(2026, 5, 26)   # ISO week 22
    late = date(2026, 6, 16)    # ISO week 25 (PERIOD)
    early_period = reconcile.current_period("weekly", early)
    late_period = reconcile.current_period("weekly", late)
    assert early_period != late_period

    r1 = reconcile.reconcile_program(
        pl.read_program(program_id, root=root), _registry(), now=early)
    assert r1["new_cycle"] is True

    r2 = reconcile.reconcile_program(
        pl.read_program(program_id, root=root), _registry(), now=late)
    assert r2["new_cycle"] is True

    body = pl.read_program(program_id, root=root)["body"]
    # BOTH cycle entries survive, in order (the real two-cycle path, not a
    # string .replace). Verdict per period is whatever computed at that `now`.
    assert f"### {early_period} -" in body
    assert f"### {late_period} -" in body
    assert body.index(f"### {early_period} -") < body.index(f"### {late_period} -")


# ─── emitters (Task 3) ────────────────────────────────────────────────────────
#
# escalate fires a LOCAL human-queue card (Tier-1, no external writes). Isolation
# is handled by the autouse _isolated_task_queues fixture above: task_lib
# resolves its queue dirs from MODULE CONSTANTS at import, so the fixture
# monkeypatches BOTH task_lib.TASKS_DIR (a tmp tasks dir with the four queue
# subdirs) AND task_lib.COUNTER_FILE (a seeded tmp _counter). The real
# datasets/tasks/ is NEVER touched.


def test_emitter_broken_creates_one_human_card(tmp_path):
    root = str(tmp_path / "data")
    program_id = _seed_broken_pipeline(root)
    program = pl.read_program(program_id, root=root)

    result = reconcile.reconcile_program(program, _registry(), now=NOW)

    assert result["verdict"] == "broken"
    assert len(result["emitted"]) == 1
    task_id = result["emitted"][0]

    cards = task_lib.list_tasks(queue="human", status="open")
    assert len(cards) == 1
    card = cards[0]
    assert card["id"] == task_id
    assert program_id in card["tags"]
    assert "cadence" in card["tags"]
    assert card["priority"] == "high"

    # The cycle-log line records the emitted task id.
    body = pl.read_program(program_id, root=root)["body"]
    cycles = body.split("## Cycles", 1)[1]
    assert task_id in cycles
    assert "emitted: none" not in cycles


def test_emitter_dedupes_on_second_force_run(tmp_path):
    root = str(tmp_path / "data")
    program_id = _seed_broken_pipeline(root)

    program = pl.read_program(program_id, root=root)
    first = reconcile.reconcile_program(program, _registry(), now=NOW)
    assert len(first["emitted"]) == 1

    # A second forced run in the same period: the open card already exists ->
    # dedupe -> NO new card.
    program = pl.read_program(program_id, root=root)
    second = reconcile.reconcile_program(program, _registry(), now=NOW, force=True)
    assert second["emitted"] == []

    cards = task_lib.list_tasks(queue="human", status="open")
    assert len(cards) == 1  # still just the one


def test_emitter_holding_emits_none(tmp_path):
    root = str(tmp_path / "data")
    program_id, _ = pl.create_program(
        type="eos-issues",
        title="Issues list",
        owner_role="ops",
        frontmatter_extra={
            "policy": 21,
            "items": [{"name": "a", "owner": "ops", "age": 3}],
            "last_cycle": OTHER_PERIOD,
        },
        root=root,
    )
    program = pl.read_program(program_id, root=root)

    result = reconcile.reconcile_program(program, _registry(), now=NOW)
    assert result["verdict"] == "holding"
    assert result["emitted"] == []
    assert task_lib.list_tasks(queue="human", status="open") == []


def test_emitter_drifting_emits_none(tmp_path):
    root = str(tmp_path / "data")
    program_id, _ = pl.create_program(
        type="eos-issues",
        title="Drifting list",
        owner_role="ops",
        frontmatter_extra={
            "policy": 21,
            "items": [{"name": "a", "owner": "ops", "age": 18}],  # 0.8*21=16.8 -> drifting
            "last_cycle": OTHER_PERIOD,
        },
        root=root,
    )
    program = pl.read_program(program_id, root=root)

    result = reconcile.reconcile_program(program, _registry(), now=NOW)
    assert result["verdict"] == "drifting"
    assert result["emitted"] == []
    assert task_lib.list_tasks(queue="human", status="open") == []


def test_list_tasks_projects_tags(tmp_path):
    # Item D: list_tasks must surface `tags` so the dedupe scan can read them.
    # (Queues isolated by the autouse fixture.)
    task_lib.create_task(title="tagged card", queue="human",
                         tags=["PROG-0001", "cadence"], creator="cadence")
    cards = task_lib.list_tasks(queue="human", status="open")
    assert len(cards) == 1
    assert "tags" in cards[0]
    assert cards[0]["tags"] == ["PROG-0001", "cadence"]


# ─── reconcile_all (Task 4) ───────────────────────────────────────────────────
#
# The portfolio-level driver: load registry once, list active programs, reconcile
# each inside a try/except so one bad program never stalls the run. Isolation is
# mandatory — every test seeds a tmp datasets root via create_program(root=tmp);
# the real datasets/programs/ is NEVER touched.


def _seed_holding_register(root, last_cycle=OTHER_PERIOD, title="Issues list"):
    """Create a register program that computes to `holding` (within policy)."""
    program_id, _ = pl.create_program(
        type="eos-issues",
        title=title,
        owner_role="ops",
        frontmatter_extra={
            "policy": 21,
            "items": [{"name": "a", "owner": "ops", "age": 3}],
            "last_cycle": last_cycle,
        },
        root=root,
    )
    return program_id


def test_reconcile_all_reconciles_active_programs(tmp_path):
    root = str(tmp_path / "data")
    p1 = _seed_holding_register(root, title="List one")
    p2 = _seed_broken_pipeline(root)

    results = reconcile.reconcile_all(root=root, now=NOW)

    assert len(results) == 2
    by_id = {r["program_id"]: r for r in results}
    assert by_id[p1]["verdict"] == "holding"
    assert by_id[p2]["verdict"] == "broken"
    assert all("error" not in r for r in results)


def test_reconcile_all_resilient_to_one_failing_program(tmp_path, monkeypatch):
    # Two well-formed active programs + one that makes reconcile_program raise.
    # Monkeypatch reconcile_program with a wrapper that raises for one specific
    # id and delegates otherwise — one bad program must NOT stall the run.
    root = str(tmp_path / "data")
    good1 = _seed_holding_register(root, title="Good one")
    good2 = _seed_broken_pipeline(root)
    bad = _seed_holding_register(root, title="Bad one")

    real = reconcile.reconcile_program

    def flaky(program, registry, **kwargs):
        if program["frontmatter"].get("program_id") == bad:
            raise RuntimeError("boom")
        return real(program, registry, **kwargs)

    monkeypatch.setattr(reconcile, "reconcile_program", flaky)

    results = reconcile.reconcile_all(root=root, now=NOW)

    assert len(results) == 3
    by_id = {r["program_id"]: r for r in results}
    # Exactly one carries an error key; it is the bad program.
    errored = [r for r in results if "error" in r]
    assert len(errored) == 1
    assert errored[0]["program_id"] == bad
    assert "boom" in errored[0]["error"]
    # The other two reconciled normally — no exception propagated.
    assert by_id[good1]["verdict"] == "holding"
    assert by_id[good2]["verdict"] == "broken"
    assert "error" not in by_id[good1]
    assert "error" not in by_id[good2]


def test_reconcile_all_skips_non_active_programs(tmp_path):
    root = str(tmp_path / "data")
    active = _seed_holding_register(root, title="Active list")
    paused, _ = pl.create_program(
        type="eos-issues",
        title="Paused list",
        owner_role="ops",
        frontmatter_extra={
            "status": "paused",
            "policy": 21,
            "items": [{"name": "a", "owner": "ops", "age": 3}],
            "last_cycle": OTHER_PERIOD,
        },
        root=root,
    )

    results = reconcile.reconcile_all(root=root, now=NOW)

    ids = {r["program_id"] for r in results}
    assert active in ids
    assert paused not in ids
    assert len(results) == 1


# ─── CLI (Task 4) ─────────────────────────────────────────────────────────────


def test_main_all_force_returns_zero(tmp_path, monkeypatch, capsys):
    # main() drives reconcile_all over the default root, so stub reconcile_all
    # for the CLI smoke test (its real behavior is covered above with root=).
    def stub(root=None, now=None, force=False):
        assert force is True
        return [
            {"program_id": "PROG-0001", "verdict": "broken",
             "new_cycle": True, "emitted": ["TASK-0123"]},
            {"program_id": "PROG-0002", "verdict": "holding",
             "new_cycle": False, "emitted": []},
        ]

    monkeypatch.setattr(reconcile, "reconcile_all", stub)

    rc = reconcile.main(["--all", "--force"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PROG-0001" in out
    assert "PROG-0002" in out
    assert "TASK-0123" in out
    # ASCII only — no em/en dash in CLI output (invariant #8).
    assert "—" not in out
    assert "–" not in out


def test_main_reports_errored_program(tmp_path, monkeypatch, capsys):
    def stub(root=None, now=None, force=False):
        return [{"program_id": "PROG-0004", "error": "boom"}]

    monkeypatch.setattr(reconcile, "reconcile_all", stub)

    rc = reconcile.main(["--all"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PROG-0004" in out
    assert "ERROR" in out
    assert "boom" in out


def test_main_bad_now_returns_nonzero(capsys):
    rc = reconcile.main(["--now", "not-a-date", "--all"])
    assert rc != 0
    err = capsys.readouterr()
    combined = err.out + err.err
    assert "not-a-date" in combined or "now" in combined.lower()


def test_main_accepts_trailing_z_in_now(tmp_path, monkeypatch):
    captured = {}

    def stub(root=None, now=None, force=False):
        captured["now"] = now
        return []

    monkeypatch.setattr(reconcile, "reconcile_all", stub)

    rc = reconcile.main(["--all", "--now", "2026-06-16T09:00:00Z"])
    assert rc == 0
    assert isinstance(captured["now"], datetime)


def test_main_without_all_returns_nonzero(capsys):
    rc = reconcile.main([])
    assert rc != 0


# ─── instrument normalization (Task 5, I2) ───────────────────────────────────

def test_instrument_is_mechanical_pm_tracker():
    assert reconcile._instrument_is_mechanical("the PM tracker") is True


def test_instrument_is_mechanical_pendo():
    assert reconcile._instrument_is_mechanical("Pendo") is True


def test_instrument_is_mechanical_deterministic_check():
    assert reconcile._instrument_is_mechanical("a deterministic check") is True


def test_instrument_is_mechanical_human_attestation_is_false():
    assert reconcile._instrument_is_mechanical("human attestation") is False


def test_instrument_is_mechanical_empty_is_false():
    assert reconcile._instrument_is_mechanical("") is False
    assert reconcile._instrument_is_mechanical(None) is False


def test_instrument_is_mechanical_ambiguous_is_false():
    assert reconcile._instrument_is_mechanical("weird thing") is False


def test_instrument_is_mechanical_manual_is_false():
    assert reconcile._instrument_is_mechanical("a manual review") is False


# ─── checkpoint-driven phase advancement (Task 5, the fact door) ─────────────
#
# roadmap-initiative phases now carry exit_checkpoint: discovery -> discovery-exit,
# execution -> ship. The fact door advances the phase when that checkpoint is
# mechanically confirmed done.

def _seed_discovery_program(root, instrument, status="met", last_cycle=OTHER_PERIOD,
                            extra_obs=None):
    """A roadmap-initiative parked in `discovery` with a discovery-exit checkpoint."""
    program_id, _ = pl.create_program(
        type="roadmap-initiative",
        title="Advance me",
        owner_role="pm",
        frontmatter_extra={
            "phase": "discovery",
            "phase_entered": {"discovery": "2026-05-01"},
            "checkpoints": [
                {"id": "discovery-exit", "label": "Discovery exit",
                 "due": "2026-09-01", "instrument": instrument, "status": status},
            ],
            "last_cycle": last_cycle,
        },
        root=root,
    )
    if extra_obs:
        pl.append_observation(program_id, root=root, **extra_obs)
    return program_id


def test_fact_door_advances_mechanical_met_checkpoint(tmp_path):
    root = str(tmp_path)
    pid = _seed_discovery_program(root, instrument="the PM tracker", status="met")
    program = pl.read_program(pid, root=root)

    reconcile.reconcile_program(program, _registry(), now=NOW)

    fm = pl.read_program(pid, root=root)["frontmatter"]
    assert fm["phase"] == "planning"          # discovery -> next
    # phase_entered keeps the dict form and stamps the new phase with today.
    assert isinstance(fm["phase_entered"], dict)
    assert fm["phase_entered"]["planning"] == NOW.isoformat()
    assert fm["phase_entered"]["discovery"] == "2026-05-01"  # prior key preserved

    body = pl.read_program(pid, root=root)["body"]
    # A completion observation citing the checkpoint was stamped.
    assert "[completion]" in body
    assert "discovery -> planning" in body
    assert "discovery-exit" in body
    # The cycle note records the advancement.
    cycles = body.split("## Cycles", 1)[1]
    assert "discovery -> planning" in cycles


def test_fact_door_does_not_advance_human_attested(tmp_path):
    root = str(tmp_path)
    pid = _seed_discovery_program(root, instrument="human attestation", status="met")
    program = pl.read_program(pid, root=root)

    reconcile.reconcile_program(program, _registry(), now=NOW)

    fm = pl.read_program(pid, root=root)["frontmatter"]
    assert fm["phase"] == "discovery"   # NOT advanced (Task 6 will propose)
    body = pl.read_program(pid, root=root)["body"]
    assert "discovery -> planning" not in body


def test_fact_door_advances_on_adapter_completion_observation(tmp_path):
    root = str(tmp_path)
    # Checkpoint mechanical but still PENDING; a fresh adapter-grounded completion
    # observation flips it to met and advances.
    pid = _seed_discovery_program(
        root, instrument="the PM tracker", status="pending",
        extra_obs=dict(kind="completion", sentinel="tracker-truth",
                       source="adapter:project_management:EPIC-204",
                       claim="Tracker reports status 'Done'."),
    )
    program = pl.read_program(pid, root=root)

    reconcile.reconcile_program(program, _registry(), now=NOW)

    fm = pl.read_program(pid, root=root)["frontmatter"]
    assert fm["phase"] == "planning"
    # The checkpoint was flipped to met (the fact, grounded in the adapter obs).
    cp = next(c for c in fm["checkpoints"] if c["id"] == "discovery-exit")
    assert cp["status"] == "met"


def test_fact_door_ignores_stale_adapter_completion(tmp_path):
    root = str(tmp_path)
    # A completion dated BEFORE the current phase was entered (2026-05-01) cannot
    # be evidence for exiting it -> no advance.
    pid = _seed_discovery_program(
        root, instrument="the PM tracker", status="pending",
        extra_obs=dict(kind="completion", sentinel="tracker-truth",
                       source="adapter:project_management:EPIC-204",
                       claim="Old close.", date="2026-04-01"),
    )
    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)
    assert pl.read_program(pid, root=root)["frontmatter"]["phase"] == "discovery"


def test_fact_door_ignores_adapter_completion_for_other_anchor(tmp_path):
    root = str(tmp_path)
    # The program's tracker anchor is EPIC-999; a completion citing EPIC-204
    # belongs to a different program -> must not advance this one.
    program_id, _ = pl.create_program(
        type="roadmap-initiative", title="Anchored", owner_role="pm",
        frontmatter_extra={
            "phase": "discovery", "phase_entered": {"discovery": "2026-05-01"},
            "checkpoints": [{"id": "discovery-exit", "label": "Discovery exit",
                             "due": "2026-09-01", "instrument": "the PM tracker",
                             "status": "pending"}],
            "bindings": [{"id": "tracker", "role": "truth",
                          "kind": "project_management", "anchor": "EPIC-999",
                          "mode": "read"}],
            "last_cycle": OTHER_PERIOD,
        }, root=root)
    pl.append_observation(program_id, root=root, kind="completion",
                          sentinel="tracker-truth",
                          source="adapter:project_management:EPIC-204",
                          claim="Different program's epic closed.")
    reconcile.reconcile_program(pl.read_program(program_id, root=root), _registry(), now=NOW)
    assert pl.read_program(program_id, root=root)["frontmatter"]["phase"] == "discovery"


def test_fact_door_pending_no_adapter_obs_does_not_advance(tmp_path):
    root = str(tmp_path)
    pid = _seed_discovery_program(root, instrument="the PM tracker", status="pending")
    program = pl.read_program(pid, root=root)

    reconcile.reconcile_program(program, _registry(), now=NOW)

    fm = pl.read_program(pid, root=root)["frontmatter"]
    assert fm["phase"] == "discovery"   # mechanical but no confirmation -> hold


def test_fact_door_terminal_phase_no_op(tmp_path):
    root = str(tmp_path)
    program_id, _ = pl.create_program(
        type="roadmap-initiative", title="Terminal", owner_role="pm",
        frontmatter_extra={
            "phase": "verified",   # terminal in registry
            "phase_entered": {"verified": "2026-05-01"},
            "checkpoints": [],
            "last_cycle": OTHER_PERIOD,
        },
        root=root,
    )
    program = pl.read_program(program_id, root=root)
    reconcile.reconcile_program(program, _registry(), now=NOW)
    fm = pl.read_program(program_id, root=root)["frontmatter"]
    assert fm["phase"] == "verified"   # never advances past terminal


def test_fact_door_idempotent_within_period(tmp_path):
    root = str(tmp_path)
    pid = _seed_discovery_program(root, instrument="the PM tracker", status="met")
    program = pl.read_program(pid, root=root)

    reconcile.reconcile_program(program, _registry(), now=NOW)
    fm = pl.read_program(pid, root=root)["frontmatter"]
    assert fm["phase"] == "planning"

    # A second reconcile, same period -> no-op (period guard) -> still planning.
    program2 = pl.read_program(pid, root=root)
    reconcile.reconcile_program(program2, _registry(), now=NOW)
    fm2 = pl.read_program(pid, root=root)["frontmatter"]
    assert fm2["phase"] == "planning"

    # And even forced same-period it does not chain past planning: planning has
    # no exit_checkpoint, so no further advance.
    program3 = pl.read_program(pid, root=root)
    reconcile.reconcile_program(program3, _registry(), now=NOW, force=True)
    fm3 = pl.read_program(pid, root=root)["frontmatter"]
    assert fm3["phase"] == "planning"


# ─── propose-update emitter (Task 6, the interpretation door) ────────────────
#
# A HUMAN-ATTESTED exit checkpoint cannot auto-flip (Task 5's fact door declines
# it). When a fresh INTERPRETIVE completion observation (a movement-watch obs
# whose source is NOT adapter:-prefixed, dated on/after phase entry) says the
# phase looks done, the reconciler emits a propose-update recommendation card a
# human accepts (Task 7). The program is NOT mutated by the proposal.

def _open_human_cards():
    """Read OPEN human-queue cards' (task_type, proposal) for assertion."""
    cards = []
    for t in task_lib.list_tasks(queue="human", status="open"):
        fm = task_lib.read_task(t["id"])["frontmatter"]
        cards.append(fm)
    return cards


def test_propose_update_emits_card_for_human_attested_with_interpretive_obs(tmp_path):
    root = str(tmp_path)
    pid = _seed_discovery_program(
        root, instrument="human attestation", status="pending",
        extra_obs=dict(kind="completion", sentinel="movement-watch",
                       source="datasets/meetings/2026-06-11_x.md (#Action Items)",
                       claim="Discovery spike reported complete in standup."),
    )

    result = reconcile.reconcile_program(
        pl.read_program(pid, root=root), _registry(), now=NOW)

    # The program is NOT mutated -- proposal only (Task 7/accept applies it).
    fm = pl.read_program(pid, root=root)["frontmatter"]
    assert fm["phase"] == "discovery"

    # Exactly one cadence-propose-update recommendation card was created.
    cards = [c for c in _open_human_cards()
             if c.get("task_type") == "cadence-propose-update"]
    assert len(cards) == 1
    card = cards[0]
    assert card["card_type"] == "recommendation"
    assert pid in card["tags"]
    assert "cadence" in card["tags"]
    assert card["proposal"] == {
        "op": "advance-phase", "to": "planning",
        "checkpoint": "discovery-exit", "from": "discovery"}
    assert card["id"] in result["emitted"]


def test_propose_update_dedupes_within_open_card(tmp_path):
    root = str(tmp_path)
    pid = _seed_discovery_program(
        root, instrument="human attestation", status="pending",
        extra_obs=dict(kind="completion", sentinel="movement-watch",
                       source="datasets/meetings/x.md",
                       claim="Looks done."),
    )
    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)
    # A second forced reconcile while the proposal card is still open -> no dup.
    reconcile.reconcile_program(
        pl.read_program(pid, root=root), _registry(), now=NOW, force=True)
    cards = [c for c in _open_human_cards()
             if c.get("task_type") == "cadence-propose-update"]
    assert len(cards) == 1


def test_propose_update_no_interpretive_obs_no_card(tmp_path):
    root = str(tmp_path)
    pid = _seed_discovery_program(root, instrument="human attestation", status="pending")
    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)
    cards = [c for c in _open_human_cards()
             if c.get("task_type") == "cadence-propose-update"]
    assert cards == []


def test_propose_update_not_emitted_when_fact_door_advances(tmp_path):
    root = str(tmp_path)
    # Mechanical + met -> the fact door advances; the proposal door must NOT also
    # fire (the checkpoint is no longer the current phase's exit after advancing,
    # and the instrument is mechanical anyway).
    pid = _seed_discovery_program(root, instrument="the PM tracker", status="met")
    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)
    fm = pl.read_program(pid, root=root)["frontmatter"]
    assert fm["phase"] == "planning"   # advanced by the fact door
    cards = [c for c in _open_human_cards()
             if c.get("task_type") == "cadence-propose-update"]
    assert cards == []


def test_propose_update_ignores_adapter_completion(tmp_path):
    root = str(tmp_path)
    # An adapter-sourced completion is the fact door's domain, not the proposal
    # door's. With a human-attested checkpoint and ONLY an adapter completion, no
    # interpretive signal exists -> no proposal.
    pid = _seed_discovery_program(
        root, instrument="human attestation", status="pending",
        extra_obs=dict(kind="completion", sentinel="tracker-truth",
                       source="adapter:project_management:EPIC-204",
                       claim="Tracker reports done."),
    )
    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)
    cards = [c for c in _open_human_cards()
             if c.get("task_type") == "cadence-propose-update"]
    assert cards == []
    # And it did not auto-advance either (human-attested instrument).
    assert pl.read_program(pid, root=root)["frontmatter"]["phase"] == "discovery"


# ─── produce-artifact emitter (Task 3, the worker-dispatch door) ─────────────
#
# On a FRESH cycle, a `produce-artifact` emitter dispatches a worker as an
# agent-queue task for this program (Tier-1: a local agent card + a detached
# task_dispatch spawn, no external write here). Deduped to once per period
# against an OPEN agent task tagged with the program_id carrying the worker's
# task_type. `_dispatch_agent_task` is a module function so tests monkeypatch it
# (no real `claude` spawn). The registry emitter wiring is Task 6; these tests
# build a minimal cycle-type registry inline so the branch is exercised alone.


def _digest_registry():
    """A minimal registry whose cycle type fires the produce-artifact emitter.

    Mirrors the weekly-priorities shape (state_model=cycle, weekly cadence) but
    carries ONLY the produce-artifact emitter so escalate never co-fires."""
    return {
        "types": [
            {
                "id": "weekly-priorities",
                "label": "Weekly priorities",
                "state_model": "cycle",
                "cadence": "weekly",
                "emitters": [
                    {
                        "on": "cycle-fresh",
                        "action": "produce-artifact",
                        "worker": "priority-digest",
                    }
                ],
            }
        ]
    }


def _seed_holding_weekly_priorities(root, last_cycle=OTHER_PERIOD):
    """Create a weekly-priorities program that computes to `holding` (no periods)."""
    program_id, _ = pl.create_program(
        type="weekly-priorities",
        title="Weekly priorities",
        owner_role="product",
        frontmatter_extra={"last_cycle": last_cycle},
        root=root,
    )
    return program_id


def test_produce_artifact_dispatches_priority_digest_agent_task(tmp_path, monkeypatch):
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    calls = []
    monkeypatch.setattr(reconcile, "_dispatch_agent_task", lambda tid: calls.append(tid))

    program = pl.read_program(program_id, root=root)
    result = reconcile.reconcile_program(
        program, _digest_registry(), now=NOW, force=True
    )

    assert result["verdict"] == "holding"
    assert len(result["emitted"]) == 1
    task_id = result["emitted"][0]

    # An agent-queue task tagged [program_id, "cadence"] with the worker task_type.
    cards = task_lib.list_tasks(queue="agent", status="open")
    assert len(cards) == 1
    card = cards[0]
    assert card["id"] == task_id
    assert card["task_type"] == "priority-digest"
    assert program_id in card["tags"]
    assert "cadence" in card["tags"]

    # The worker was dispatched exactly once, for that task id.
    assert calls == [task_id]


def test_produce_artifact_deduped_within_period(tmp_path, monkeypatch):
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    monkeypatch.setattr(reconcile, "_dispatch_agent_task", lambda tid: None)

    # First fresh cycle: one priority-digest agent task created.
    program = pl.read_program(program_id, root=root)
    first = reconcile.reconcile_program(
        program, _digest_registry(), now=NOW, force=True
    )
    assert len(first["emitted"]) == 1

    # A second forced run in the SAME period: the open digest task already exists
    # -> dedupe -> no second task, no second dispatch.
    calls = []
    monkeypatch.setattr(reconcile, "_dispatch_agent_task", lambda tid: calls.append(tid))
    program = pl.read_program(program_id, root=root)
    second = reconcile.reconcile_program(
        program, _digest_registry(), now=NOW, force=True
    )
    assert second["emitted"] == []
    assert calls == []

    cards = task_lib.list_tasks(queue="agent", status="open")
    assert len(cards) == 1  # still just the one


# ─── portfolio-rollup >=2-families dispatch gate (inc5 slice 11) ─────────────
#
# The cross-program rollup's produce-artifact emitter carries min_active_families.
# It fires only once the portfolio spans >= N active families OTHER than the
# rollup's own (system) family; below the threshold it stays inert (no empty
# digest). Family count is read from the program store (real registry), never a
# literal, so the seeded programs use real types.


def _rollup_registry():
    return {"types": [{
        "id": "portfolio-rollup", "label": "Portfolio rollup",
        "state_model": "cycle", "cadence": "weekly", "family": "system",
        "emitters": [{
            "on": "cycle-fresh", "action": "produce-artifact",
            "worker": "portfolio-rollup", "min_active_families": 2,
        }],
    }]}


def test_portfolio_rollup_fires_when_two_families_active(tmp_path, monkeypatch):
    root = str(tmp_path / "data")
    # Two non-system families active in the store (roadmap + weekly).
    pl.create_program(type="roadmap-initiative", title="R", owner_role="product", root=root)
    pl.create_program(type="weekly-priorities", title="W", owner_role="product", root=root)
    rollup_id, _ = pl.create_program(
        type="portfolio-rollup", title="Rollup", owner_role="product",
        frontmatter_extra={"last_cycle": OTHER_PERIOD}, root=root)
    monkeypatch.setattr(reconcile, "_dispatch_agent_task", lambda tid: None)

    program = pl.read_program(rollup_id, root=root)
    result = reconcile.reconcile_program(program, _rollup_registry(), now=NOW, force=True, root=root)

    assert len(result["emitted"]) == 1
    card = task_lib.list_tasks(queue="agent", status="open")[0]
    assert card["task_type"] == "portfolio-rollup"
    assert rollup_id in card["tags"]


def test_portfolio_rollup_inert_below_two_families(tmp_path, monkeypatch):
    root = str(tmp_path / "data")
    # Only ONE non-system family active -> the rollup stays inert.
    pl.create_program(type="roadmap-initiative", title="R", owner_role="product", root=root)
    rollup_id, _ = pl.create_program(
        type="portfolio-rollup", title="Rollup", owner_role="product",
        frontmatter_extra={"last_cycle": OTHER_PERIOD}, root=root)
    monkeypatch.setattr(reconcile, "_dispatch_agent_task", lambda tid: None)

    program = pl.read_program(rollup_id, root=root)
    result = reconcile.reconcile_program(program, _rollup_registry(), now=NOW, force=True, root=root)

    assert result["emitted"] == []   # inert: <2 active families
    assert task_lib.list_tasks(queue="agent", status="open") == []


# ─── draft-message emitter (Task 4, the rate-capped nudge door) ──────────────
#
# On a FRESH cycle, a `draft-message` emitter creates a send-message COLLAB card
# from a template, ENFORCING max_nudges_per_person_per_week per recipient and
# recording a per-period response-rate counter on the program frontmatter. The
# recipient is resolved profile-driven (degrades to a role-based target from the
# program's owner_role) -- never a person/company literal. Tier-1: a local
# collab card only; the actual send is the existing Tier-2 path. The registry
# emitter wiring is a later task; these tests build a minimal cycle-type
# registry inline so the branch is exercised alone.


def _nudge_registry(cap=1):
    """A minimal cycle-type registry whose only emitter is a draft-message nudge.

    Carries ONLY the draft-message emitter so escalate/produce-artifact never
    co-fire. `cap` sets max_nudges_per_person_per_week (None -> unlimited)."""
    emitter = {"on": "cycle-fresh", "action": "draft-message", "template": "nudge"}
    if cap is not None:
        emitter["max_nudges_per_person_per_week"] = cap
    return {
        "types": [
            {
                "id": "weekly-priorities",
                "label": "Weekly priorities",
                "state_model": "cycle",
                "cadence": "weekly",
                "emitters": [emitter],
            }
        ]
    }


def test_draft_message_creates_send_message_collab_card(tmp_path):
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    program = pl.read_program(program_id, root=root)
    result = reconcile.reconcile_program(
        program, _nudge_registry(cap=1), now=NOW, force=True
    )

    assert len(result["emitted"]) == 1
    task_id = result["emitted"][0]

    cards = task_lib.list_tasks(queue="collab", status="open")
    assert len(cards) == 1
    card = cards[0]
    assert card["id"] == task_id
    assert card["task_type"] == "send-message"
    assert program_id in card["tags"]
    assert "cadence" in card["tags"]

    # The recipient is a role-based target (no literal); the body re-reads it.
    fm_card = task_lib.read_task(task_id)["frontmatter"]
    assert fm_card.get("message_to")  # a recipient string is set
    # owner_role was "product" -> the role-based target references it.
    assert "product" in fm_card["message_to"]
    # The card must carry the nudge text in message_body — the shipper builds the
    # outgoing draft from message_body, so an empty one would send a blank nudge.
    assert fm_card.get("message_body")  # a NON-EMPTY wire body is set
    assert program_id in fm_card["message_body"]


def test_draft_message_respects_nudge_cap_and_suppresses(tmp_path):
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    # First fresh cycle: one send-message collab card created for the recipient.
    program = pl.read_program(program_id, root=root)
    first = reconcile.reconcile_program(
        program, _nudge_registry(cap=1), now=NOW, force=True
    )
    assert len(first["emitted"]) == 1

    # A second forced run in the SAME period: the recipient is already at the
    # cap (1/wk) -> SUPPRESSED. No new card; the suppression is recorded.
    program = pl.read_program(program_id, root=root)
    second = reconcile.reconcile_program(
        program, _nudge_registry(cap=1), now=NOW, force=True
    )

    # No real card id was created on the second run.
    real_ids = [e for e in second["emitted"] if e and e.startswith("TASK-")]
    assert real_ids == []
    cards = task_lib.list_tasks(queue="collab", status="open")
    assert len(cards) == 1  # still just the one from the first cycle

    # The suppression is visible in the cycle log (the emitted: clause).
    body = pl.read_program(program_id, root=root)["body"]
    cycles_section = body.split("## Cycles", 1)[1]
    assert "nudge suppressed (cap 1/wk)" in cycles_section


def test_draft_message_records_nudge_counter(tmp_path):
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    program = pl.read_program(program_id, root=root)
    reconcile.reconcile_program(program, _nudge_registry(cap=1), now=NOW, force=True)

    fm = pl.read_program(program_id, root=root)["frontmatter"]
    counts = fm.get("nudge_counts") or {}
    period_counts = counts.get(PERIOD) or {}
    # Exactly one recipient counted, with a count of 1 this period.
    assert period_counts
    assert sum(period_counts.values()) == 1
    assert all(v == 1 for v in period_counts.values())
    # A response_rate stub is present for the UI to read.
    assert "response_rate" in fm


def test_draft_message_cap_is_period_scoped_not_lifetime(tmp_path):
    """The cap is N-per-recipient-PER-PERIOD, never N-per-recipient-ever.

    Regression for the rate-cap scoping bug: the cap was enforced by scanning
    OPEN send-message cards for the recipient, with NO period filter. In this
    system messaging is normally unconfigured (channel `none`), so a created
    send-message card never sends and stays `open` indefinitely. That meant the
    FIRST period's nudge card suppressed EVERY later period's nudge -- turning
    "max 1 per week" into "max 1 ever".

    Here we plant a stale OPEN send-message card for the recipient from a PRIOR
    period (it never sent), AND a prior-period entry in nudge_counts already at
    the cap. Then we run a draft-message in a NEW period whose counter is 0. A
    card MUST be created: the prior period's open card / count does not suppress
    the new period. Fails against the open-card scan; passes once the cap reads
    the period-keyed counter.
    """
    root = str(tmp_path / "data")
    # Program last reconciled in OTHER_PERIOD, with a prior-period nudge already
    # recorded at the cap for the role-based recipient ("product team").
    recipient = "product team"
    program_id, _ = pl.create_program(
        type="weekly-priorities",
        title="Weekly priorities",
        owner_role="product",
        frontmatter_extra={
            "last_cycle": OTHER_PERIOD,
            "nudge_counts": {OTHER_PERIOD: {recipient: 1}},
        },
        root=root,
    )

    # A stale OPEN send-message card from the prior period that never sent.
    task_lib.create_task(
        title="prior-period nudge",
        queue="collab",
        creator="cadence",
        task_type="send-message",
        tags=[program_id, "cadence"],
        message_channel="none",
        message_to=recipient,
    )

    # Run a fresh cycle in the CURRENT period (NOW -> PERIOD != OTHER_PERIOD).
    program = pl.read_program(program_id, root=root)
    result = reconcile.reconcile_program(
        program, _nudge_registry(cap=1), now=NOW, force=True
    )

    # A new card IS created -- the prior period's open card / count does not
    # suppress this period's nudge.
    real_ids = [e for e in result["emitted"] if e and e.startswith("TASK-")]
    assert len(real_ids) == 1, "prior-period nudge wrongly suppressed this period"

    # The counter for THIS period now reflects the one new nudge.
    fm = pl.read_program(program_id, root=root)["frontmatter"]
    period_counts = (fm.get("nudge_counts") or {}).get(PERIOD) or {}
    assert period_counts.get(recipient) == 1
    # The prior period's entry is untouched (per-period scoping is preserved).
    assert (fm.get("nudge_counts") or {}).get(OTHER_PERIOD, {}).get(recipient) == 1


def test_fire_weekday_blocks_wrong_day(tmp_path):
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)
    reg = _nudge_registry(cap=1)
    reg["types"][0]["emitters"][0]["fire_weekday"] = 3  # Wednesday
    program = pl.read_program(program_id, root=root)
    result = reconcile.reconcile_program(program, reg, now=NOW, force=True)
    real_ids = [e for e in result["emitted"] if e and e.startswith("TASK-")]
    assert len(real_ids) == 0  # NOW is Tuesday (2), emitter wants Wednesday (3)


def test_fire_weekday_allows_matching_day(tmp_path):
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)
    reg = _nudge_registry(cap=1)
    reg["types"][0]["emitters"][0]["fire_weekday"] = 2  # Tuesday = NOW
    program = pl.read_program(program_id, root=root)
    result = reconcile.reconcile_program(program, reg, now=NOW, force=True)
    real_ids = [e for e in result["emitted"] if e and e.startswith("TASK-")]
    assert len(real_ids) == 1


# ─── birth proposals (inc4a Task 6, the birth path) ──────────────────────────
#
# The reconciler already processes the program-intake register. _propose_births
# walks its OPEN candidates: a candidate is ripe when its target type's
# intake.birth_threshold is crossed (source counting, or an explicit-declaration
# marker for declaration-gated types). A ripe candidate emits ONE recommendation/
# cadence-propose-update card carrying proposal {op: "birth", ...}, deduped by
# candidate_id (all births share op "birth", so op-dedupe is WRONG here).


def _seed_intake_program(root, items, last_cycle=OTHER_PERIOD):
    """Seed a program-intake register program carrying `items` candidates."""
    program_id, _ = pl.create_program(
        type="program-intake",
        title="Program intake",
        owner_role="product",
        frontmatter_extra={
            "items": items,
            "policy": 30,
            "last_cycle": last_cycle,
        },
        root=root,
    )
    return program_id


def _candidate(cid, program_type, title, sources, *, status="open", declared=None):
    """Build a candidate register item with one evidence entry per source."""
    evidence = [
        {"date": "2026-06-10", "source": s, "claim": f"signal from {s}",
         "sentinel": "program-intake"}
        for s in sources
    ]
    cand = {
        "id": cid,
        "program_type": program_type,
        "title": title,
        "anchor": None,
        "status": status,
        "evidence": evidence,
        "source_count": len(set(sources)),
    }
    if declared is not None:
        cand["declared"] = declared
    return cand


def _birth_cards():
    """Read OPEN human-queue birth proposal cards (cadence-propose-update, op birth)."""
    out = []
    for t in task_lib.list_tasks(queue="human", status="open"):
        fm = task_lib.read_task(t["id"])["frontmatter"]
        if fm.get("task_type") != "cadence-propose-update":
            continue
        prop = fm.get("proposal") or {}
        if isinstance(prop, dict) and prop.get("op") == "birth":
            out.append(fm)
    return out


def test_intake_reconcile_ages_open_candidates(tmp_path):
    # 4a M-3: an open candidate carries `opened`, not `age`. Reconcile must derive
    # `age` from `opened` so the register verdict can drift on a stale nursery.
    root = str(tmp_path / "data")
    # opened 35 days before NOW (2026-06-16) -> 2026-05-12; policy 30 -> broken.
    stale = _candidate("CAND-0001", "roadmap-initiative", "Stale candidate",
                       ["meeting-a.md"], status="open")  # 1 source -> no birth
    stale["opened"] = "2026-05-12"
    pid = _seed_intake_program(root, [stale])

    res = reconcile.reconcile_program(
        pl.read_program(pid, root=root), _registry(), now=NOW)

    item = pl.read_program(pid, root=root)["frontmatter"]["items"][0]
    assert item["age"] == 35  # derived from opened
    assert res["verdict"] == "broken"  # age 35 > policy 30


# ─── Portfolio-health janitor (Task 8) ──────────────────────────────────────

def test_scan_portfolio_health_flags_blind_sentinel(tmp_path):
    root = str(tmp_path / "data")
    import sentinel_runner
    # A sentinel that ran but errored is BLIND -> broken finding (escalates).
    sentinel_runner.record_sentinel_run(
        "movement-watch", success=False, emitted_count=0,
        error="dispatch failed", root=root, now="2026-06-15")
    findings = reconcile._scan_portfolio_health(root, NOW)
    blind = [f for f in findings if f["kind"] == "blind-sentinel"]
    assert len(blind) == 1
    assert blind[0]["severity"] == "broken"
    assert "movement-watch" in blind[0]["owner"]


def test_scan_portfolio_health_flags_stale_active(tmp_path):
    root = str(tmp_path / "data")
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="Old initiative", owner_role="pm",
        frontmatter_extra={"phase": "execution",
                           "phase_entered": {"execution": "2026-04-01"}},
        root=root)
    # one observation ~50 days before NOW; threshold = 6 cycles * 7d = 42d.
    pl.append_observation(pid, kind="status-signal", sentinel="movement-watch",
                          source="m.md", claim="early signal.",
                          date="2026-04-27", root=root)
    findings = reconcile._scan_portfolio_health(root, NOW)
    stale = [f for f in findings if f["kind"] == "stale-active"]
    assert len(stale) == 1
    assert stale[0]["owner"] == pid
    assert stale[0]["severity"] == "drifting"


def test_scan_portfolio_health_flags_aging_candidate(tmp_path):
    root = str(tmp_path / "data")
    pl.create_program(
        type="program-intake", title="Program intake", owner_role="product",
        frontmatter_extra={"policy": 30, "items": [
            {"id": "CAND-0001", "program_type": "roadmap-initiative",
             "title": "Old candidate", "status": "open", "opened": "2026-04-01",
             "evidence": [], "source_count": 1}]},
        root=root)
    findings = reconcile._scan_portfolio_health(root, NOW)
    aging = [f for f in findings if f["kind"] == "aging-candidate"]
    assert len(aging) == 1
    assert aging[0]["owner"] == "CAND-0001"


def test_scan_portfolio_health_flags_duplicates(tmp_path):
    root = str(tmp_path / "data")
    pl.create_program(type="roadmap-initiative", title="Smart Reconciliation",
                      owner_role="pm", root=root)
    pl.create_program(type="roadmap-initiative", title="smart reconciliation",
                      owner_role="pm", root=root)
    findings = reconcile._scan_portfolio_health(root, NOW)
    dups = [f for f in findings if f["kind"] == "duplicate"]
    assert len(dups) == 1


def test_reconcile_portfolio_health_refreshes_items_and_escalates_blind(tmp_path):
    root = str(tmp_path / "data")
    import sentinel_runner
    sentinel_runner.record_sentinel_run(
        "movement-watch", success=False, emitted_count=0, error="boom",
        root=root, now="2026-06-15")
    pid, _ = pl.create_program(
        type="portfolio-health", title="Portfolio health", owner_role="product",
        frontmatter_extra={"items": [], "policy": 30, "last_cycle": OTHER_PERIOD},
        root=root)

    res = reconcile.reconcile_program(
        pl.read_program(pid, root=root), _registry(), now=NOW, root=root)

    # items refreshed from the live scan; the blind sentinel is among them
    items = pl.read_program(pid, root=root)["frontmatter"]["items"]
    assert any(it.get("kind") == "blind-sentinel" for it in items)
    assert res["verdict"] == "broken"
    # the drift:broken emitter escalated exactly one human card
    assert len(task_lib.list_tasks(queue="human", status="open")) == 1
    # a second reconcile dedupes the escalate card (no duplicate)
    reconcile.reconcile_program(
        pl.read_program(pid, root=root), _registry(), now=NOW, force=True, root=root)
    assert len(task_lib.list_tasks(queue="human", status="open")) == 1


def test_birth_proposed_for_ripe_candidate_only(tmp_path):
    root = str(tmp_path / "data")
    # roadmap-initiative birth_threshold: min_independent_sources 2.
    ripe = _candidate("CAND-0001", "roadmap-initiative", "Smart reconciliation",
                      ["meeting-a.md", "meeting-b.md"])           # 2 sources -> ripe
    unripe = _candidate("CAND-0002", "roadmap-initiative", "Maybe later",
                        ["meeting-c.md"])                          # 1 source -> not ripe
    pid = _seed_intake_program(root, [ripe, unripe])

    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)

    cards = _birth_cards()
    assert len(cards) == 1
    card = cards[0]
    assert card["card_type"] == "recommendation"
    assert pid in card["tags"]
    assert "cadence" in card["tags"]
    prop = card["proposal"]
    assert prop["op"] == "birth"
    assert prop["candidate_id"] == "CAND-0001"
    assert prop["program_type"] == "roadmap-initiative"
    assert prop["title"] == "Smart reconciliation"
    assert prop["checkpoints"] == []
    assert set(prop["citations"]) == {"meeting-a.md", "meeting-b.md"}


def test_birth_dedupes_by_candidate_id(tmp_path):
    root = str(tmp_path / "data")
    ripe = _candidate("CAND-0001", "roadmap-initiative", "Smart reconciliation",
                      ["meeting-a.md", "meeting-b.md"])
    pid = _seed_intake_program(root, [ripe])

    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)
    # A second forced reconcile with the birth proposal still open -> no dup.
    reconcile.reconcile_program(
        pl.read_program(pid, root=root), _registry(), now=NOW, force=True)

    assert len(_birth_cards()) == 1


def test_birth_explicit_declaration_only_ripe_when_declared(tmp_path):
    root = str(tmp_path / "data")
    # eos-rock birth_threshold: explicit_declaration_only (never source-counting).
    declared = _candidate("CAND-0001", "eos-rock", "Q3 rock",
                          ["leadership-session.md"], declared=True)   # 1 source + declared
    pid = _seed_intake_program(root, [declared])

    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)

    cards = _birth_cards()
    assert len(cards) == 1
    assert cards[0]["proposal"]["candidate_id"] == "CAND-0001"
    assert cards[0]["proposal"]["program_type"] == "eos-rock"


def test_birth_explicit_declaration_only_not_ripe_without_flag(tmp_path):
    root = str(tmp_path / "data")
    # Same eos-rock candidate, NOT declared and only 1 source -> never ripe
    # (declaration-only types never birth by source counting).
    not_declared = _candidate("CAND-0001", "eos-rock", "Q3 rock",
                              ["leadership-session.md"])
    # Even with 2 sources it must not birth (source-counting is disallowed here).
    two_sources = _candidate("CAND-0002", "eos-rock", "Q4 rock",
                             ["session-a.md", "session-b.md"])
    pid = _seed_intake_program(root, [not_declared, two_sources])

    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)

    assert _birth_cards() == []


def test_birth_declared_flows_from_upsert_to_ripeness(tmp_path):
    # Integration: a candidate written via the REAL upsert_candidate carrying
    # declared=True ripens an explicit-declaration-only type (eos-rock) and births.
    root = str(tmp_path / "data")
    pid = _seed_intake_program(root, [])
    pl.upsert_candidate(
        pid, candidate_key="k1", program_type="eos-rock", title="Q3 rock",
        source="leadership-session.md", claim="We are committing to this rock.",
        declared=True, root=root)

    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)

    cards = _birth_cards()
    assert len(cards) == 1
    assert cards[0]["proposal"]["candidate_id"] == "CAND-0001"
    assert cards[0]["proposal"]["program_type"] == "eos-rock"


def test_birth_undeclared_upsert_does_not_birth_declaration_only(tmp_path):
    # Integration: same path WITHOUT declared -> eos-rock never ripens.
    root = str(tmp_path / "data")
    pid = _seed_intake_program(root, [])
    pl.upsert_candidate(
        pid, candidate_key="k1", program_type="eos-rock", title="Q3 rock",
        source="leadership-session.md", claim="Mentioned a possible rock.",
        root=root)

    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)

    assert _birth_cards() == []


def test_birth_skips_closed_and_birthed_candidates(tmp_path):
    root = str(tmp_path / "data")
    closed = _candidate("CAND-0001", "roadmap-initiative", "Declined idea",
                        ["meeting-a.md", "meeting-b.md"], status="closed-with-reason")
    birthed = _candidate("CAND-0002", "roadmap-initiative", "Already born",
                         ["meeting-c.md", "meeting-d.md"], status="birthed")
    pid = _seed_intake_program(root, [closed, birthed])

    reconcile.reconcile_program(pl.read_program(pid, root=root), _registry(), now=NOW)

    assert _birth_cards() == []


# ─── archive door (Task 4) ─────────────────────────────────────────────────────
#
# The fact archive door: a program can be archived based on facts (terminal phase,
# verified "did-it-work" checkpoint, or tracker-closed observation). The proposal
# is deduplicated against any open propose-update card with op=="archive".


def test_propose_archive_fires_on_terminal_phase(tmp_path):
    # Create a pipeline program at its terminal phase (verified for
    # roadmap-initiative). Call _propose_archive and assert it returns a dict
    # with op=="archive" and a reason mentioning "terminal".
    root = str(tmp_path / "data")
    program_id, _ = pl.create_program(
        type="roadmap-initiative",
        title="Shipped initiative",
        owner_role="pm",
        frontmatter_extra={
            "phase": "verified",  # terminal phase for roadmap-initiative
            "phase_entered": {"verified": "2026-06-01"},
            "checkpoints": [],
        },
        root=root,
    )
    program = pl.read_program(program_id, root=root)
    fm = program["frontmatter"]
    type_entry = next((t for t in _registry()["types"] if t["id"] == "roadmap-initiative"), {})

    result = reconcile._propose_archive(fm, type_entry, program["body"])

    assert result is not None
    assert isinstance(result, dict)
    assert result.get("op") == "archive"
    assert "terminal" in result.get("reason", "").lower()
    assert isinstance(result.get("citations"), list)


def test_propose_archive_fires_on_did_it_work_verified(tmp_path):
    # Create a program with a checkpoint of kind/id containing "did-it-work"
    # with status "verified". Call _propose_archive and assert it returns
    # archive mutation with op=="archive".
    root = str(tmp_path / "data")
    program_id, _ = pl.create_program(
        type="did-it-work",
        title="Target verification",
        owner_role="pm",
        frontmatter_extra={
            "checkpoints": [
                {"id": "did-it-work", "kind": "completion", "status": "verified"},
            ],
        },
        root=root,
    )
    program = pl.read_program(program_id, root=root)
    fm = program["frontmatter"]
    type_entry = next((t for t in _registry()["types"] if t["id"] == "did-it-work"), {})

    result = reconcile._propose_archive(fm, type_entry, program["body"])

    assert result is not None
    assert result.get("op") == "archive"
    assert "did-it-work" in result.get("reason", "").lower()


def test_propose_archive_fires_on_tracker_closed_observation(tmp_path):
    # Fact 3: a `completion` observation (real "### <date> - sentinel:NAME
    # [completion]" format, NOT a "- " bullet) whose claim reports the tracker
    # closed -> archive proposal. Isolated from Fact 1/2: mid-phase, no
    # did-it-work checkpoint, so ONLY the tracker-closed fact can fire.
    root = str(tmp_path / "data")
    program_id, _ = pl.create_program(
        type="roadmap-initiative",
        title="Tracker-closed initiative",
        owner_role="pm",
        frontmatter_extra={
            "phase": "execution",  # not terminal
            "phase_entered": {"execution": "2026-06-01"},
            "checkpoints": [],
        },
        root=root,
    )
    pl.append_observation(
        program_id, kind="completion", sentinel="tracker-truth",
        source="tracker:EPIC-1", claim="Epic EPIC-1 closed.",
        date="2026-06-10", root=root,
    )
    program = pl.read_program(program_id, root=root)
    type_entry = next((t for t in _registry()["types"] if t["id"] == "roadmap-initiative"), {})

    result = reconcile._propose_archive(program["frontmatter"], type_entry, program["body"])

    assert result is not None
    assert result.get("op") == "archive"
    assert "closed" in result.get("reason", "").lower()
    # cites the observation's sentinel, not a bare placeholder
    assert any("tracker-truth" in str(c) for c in result.get("citations", []))


def test_propose_archive_none_when_active_midphase(tmp_path):
    # Create a pipeline program in a mid-pipeline phase (not terminal).
    # Call _propose_archive and assert it returns None (no archive).
    root = str(tmp_path / "data")
    program_id, _ = pl.create_program(
        type="roadmap-initiative",
        title="In progress initiative",
        owner_role="pm",
        frontmatter_extra={
            "phase": "execution",  # not terminal for roadmap-initiative
            "phase_entered": {"execution": "2026-06-01"},
            "checkpoints": [],
        },
        root=root,
    )
    program = pl.read_program(program_id, root=root)
    fm = program["frontmatter"]
    type_entry = next((t for t in _registry()["types"] if t["id"] == "roadmap-initiative"), {})

    result = reconcile._propose_archive(fm, type_entry, program["body"])

    assert result is None


def test_evaluate_emitters_creates_archive_card_and_dedupes(tmp_path):
    # Create a type with emitter: {on:"completion-verified", action:"propose-update"}
    # (using roadmap-initiative). Create an active terminal-phase program.
    # Call _evaluate_emitters and assert ONE cadence-propose-update card is
    # created with proposal.op == "archive" and tags [program_id, "cadence"].
    # Call _evaluate_emitters again on the same program and assert NO duplicate
    # card is created (op-dedupe via _open_propose_update_ops).
    root = str(tmp_path / "data")
    # For this test to work, we need a type with the archive emitter. roadmap-initiative
    # does not have one by default, so we'll manually check if the logic works by calling
    # _evaluate_emitters with a mock type_entry.
    program_id, _ = pl.create_program(
        type="roadmap-initiative",
        title="Complete initiative",
        owner_role="pm",
        frontmatter_extra={
            "phase": "verified",  # terminal
            "phase_entered": {"verified": "2026-06-01"},
            "checkpoints": [],
        },
        root=root,
    )
    program = pl.read_program(program_id, root=root)
    registry = _registry()
    type_entry = next((t for t in registry["types"] if t["id"] == "roadmap-initiative"), {})

    # Add a completion-verified emitter to the type entry for this test
    if "emitters" not in type_entry:
        type_entry["emitters"] = []
    type_entry["emitters"].append({
        "action": "propose-update",
        "on": "completion-verified"
    })

    # First call to _evaluate_emitters
    fm = program["frontmatter"]
    body = program["body"]
    period = reconcile.current_period("weekly", NOW)
    emitted_1 = reconcile._evaluate_emitters(
        program, type_entry, "holding", {}, body=body, root=root,
        period=period, registry=registry
    )

    # Should have created one card
    assert len(emitted_1) == 1
    task_id_1 = emitted_1[0]

    # Verify the card exists and has the right properties
    cards = task_lib.list_tasks(queue="human", status="open")
    assert len(cards) == 1
    card = cards[0]
    assert card["id"] == task_id_1
    assert program_id in card["tags"]
    assert "cadence" in card["tags"]

    # Verify the proposal op is "archive"
    card_full = task_lib.read_task(task_id_1)
    proposal = card_full["frontmatter"].get("proposal", {})
    assert proposal.get("op") == "archive"

    # Second call to _evaluate_emitters: should dedupe
    emitted_2 = reconcile._evaluate_emitters(
        program, type_entry, "holding", {}, body=body, root=root,
        period=period, registry=registry
    )

    # Should NOT create a new card (dedupe)
    assert len(emitted_2) == 0

    # Verify still only one card in queue
    cards = task_lib.list_tasks(queue="human", status="open")
    assert len(cards) == 1


# ─── silent archive door (Task 6) ────────────────────────────────────────────

def test_propose_archive_silent_fires_when_silent_and_sentinel_live(tmp_path):
    """Silent archive: program dormant 42+ days with live sentinel triggers proposal."""
    root = str(tmp_path / "data")
    # Create a weekly cadence type with archive_after_silent_cycles = 6 (42 days)
    type_entry = {
        "id": "test-silent-type",
        "cadence": "weekly",
        "archive_after_silent_cycles": 6,
    }

    # Create a program with latest observation 50 days ago (> 42 days silent)
    long_ago = (NOW.toordinal() - 50)
    old_date = date.fromordinal(long_ago).isoformat()

    program_id, _ = pl.create_program(
        type="test-silent-type",
        title="Dormant program",
        owner_role="pm",
        frontmatter_extra={
            "phase": "execution",
            "phase_entered": {"execution": "2026-01-01"},
            "checkpoints": [],
            "last_cycle": OTHER_PERIOD,
        },
        root=root,
    )

    # Append an observation dated 50 days ago
    body = f"## Observations\n\n### {old_date}\nDormant test observation"
    program = pl.read_program(program_id, root=root)
    fm = program["frontmatter"]
    pl._write_program_file(program["filepath"], fm, body)

    # Create telemetry showing movement-watch sentinel has live last_run
    import sentinel_runner
    sentinel_runs_path = sentinel_runner._sentinel_runs_path(root)
    os.makedirs(os.path.dirname(sentinel_runs_path), exist_ok=True)
    with open(sentinel_runs_path, "w") as f:
        json.dump({
            "movement-watch": {
                "last_run": NOW.isoformat(),
                "last_error": None,
            }
        }, f)

    # Call _propose_archive_silent
    telemetry = sentinel_runner.read_sentinel_runs(root)
    result = reconcile._propose_archive_silent(fm, type_entry, body, telemetry, NOW.isoformat())

    # Should return archive mutation
    assert result is not None
    assert isinstance(result, dict)
    assert result.get("op") == "archive"
    assert "dormant" in result.get("reason", "").lower() or "silent" in result.get("reason", "").lower()
    assert isinstance(result.get("citations"), list)


def test_propose_archive_silent_accepts_datetime_isoformat_clock(tmp_path):
    """Regression: the scheduler passes now_iso as a FULL datetime isoformat
    (e.g. '2026-06-18T19:05:48+00:00'), not a date. _propose_archive_silent must
    parse the date part rather than crash on None - date (the live-board bug)."""
    root = str(tmp_path / "data")
    type_entry = {"id": "test-silent-type", "cadence": "weekly",
                  "archive_after_silent_cycles": 6}
    old_date = date.fromordinal(NOW.toordinal() - 50).isoformat()
    pid, _ = pl.create_program(
        type="test-silent-type", title="Dormant", owner_role="pm",
        frontmatter_extra={"phase": "execution", "checkpoints": []}, root=root)
    body = f"## Observations\n\n### {old_date}\ndormant observation"
    fm = pl.read_program(pid, root=root)["frontmatter"]
    telemetry = {"movement-watch": {"last_run": NOW.isoformat(), "last_error": None}}

    # full datetime isoformat with tz offset, exactly as the scheduler builds it
    result = reconcile._propose_archive_silent(
        fm, type_entry, body, telemetry, "2026-06-16T19:05:48+00:00")

    assert result is not None  # parsed the date part, did not crash
    assert result["op"] == "archive"


def test_propose_archive_silent_suppressed_when_sentinel_blind(tmp_path):
    """Silent archive suppressed when sentinel is blind (no run / stale / error)."""
    root = str(tmp_path / "data")
    type_entry = {
        "id": "test-silent-type",
        "cadence": "weekly",
        "archive_after_silent_cycles": 6,
    }

    # Create dormant program (50 days since last observation)
    long_ago = (NOW.toordinal() - 50)
    old_date = date.fromordinal(long_ago).isoformat()

    program_id, _ = pl.create_program(
        type="test-silent-type",
        title="Dormant program",
        owner_role="pm",
        frontmatter_extra={
            "phase": "execution",
            "phase_entered": {"execution": "2026-01-01"},
            "checkpoints": [],
        },
        root=root,
    )

    # Parseable observation (### header) so the silent threshold IS crossed and
    # the test genuinely reaches the blind-sentinel suppression branch.
    body = f"## Observations\n\n### {old_date}\ndormant observation"
    program = pl.read_program(program_id, root=root)
    fm = program["frontmatter"]

    # Create telemetry with BLIND sentinel (has error)
    import sentinel_runner
    sentinel_runs_path = sentinel_runner._sentinel_runs_path(root)
    os.makedirs(os.path.dirname(sentinel_runs_path), exist_ok=True)
    with open(sentinel_runs_path, "w") as f:
        json.dump({
            "movement-watch": {
                "last_run": "2026-06-01",  # stale
                "last_error": "something went wrong",
            }
        }, f)

    telemetry = sentinel_runner.read_sentinel_runs(root)
    result = reconcile._propose_archive_silent(fm, type_entry, body, telemetry, NOW.isoformat())

    # Should return None (suppressed)
    assert result is None


def test_propose_archive_silent_none_when_recently_active(tmp_path):
    """Silent archive not proposed when program is recently active."""
    root = str(tmp_path / "data")
    type_entry = {
        "id": "test-silent-type",
        "cadence": "weekly",
        "archive_after_silent_cycles": 6,
    }

    # Create program with recent observation (5 days ago, < 42 days)
    recent = (NOW.toordinal() - 5)
    recent_date = date.fromordinal(recent).isoformat()

    program_id, _ = pl.create_program(
        type="test-silent-type",
        title="Recent program",
        owner_role="pm",
        frontmatter_extra={
            "phase": "execution",
            "phase_entered": {"execution": "2026-06-01"},
            "checkpoints": [],
        },
        root=root,
    )

    # Parseable observation so the test reaches the recently-active threshold check.
    body = f"## Observations\n\n### {recent_date}\nrecent observation"
    program = pl.read_program(program_id, root=root)
    fm = program["frontmatter"]

    # Create telemetry with live sentinel
    import sentinel_runner
    sentinel_runs_path = sentinel_runner._sentinel_runs_path(root)
    os.makedirs(os.path.dirname(sentinel_runs_path), exist_ok=True)
    with open(sentinel_runs_path, "w") as f:
        json.dump({
            "movement-watch": {
                "last_run": NOW.isoformat(),
                "last_error": None,
            }
        }, f)

    telemetry = sentinel_runner.read_sentinel_runs(root)
    result = reconcile._propose_archive_silent(fm, type_entry, body, telemetry, NOW.isoformat())

    # Should return None (recently active)
    assert result is None


def test_silent_and_fact_dedupe_to_one_archive_card(tmp_path):
    """Both fact and silent archive doors fire but dedupe to ONE card."""
    root = str(tmp_path / "data")

    # Create a type with BOTH emitters: completion-verified AND silent-too-long
    registry = _registry()
    type_entry = next((t for t in registry["types"] if t["id"] == "roadmap-initiative"), {}).copy()
    type_entry["archive_after_silent_cycles"] = 6

    if "emitters" not in type_entry:
        type_entry["emitters"] = []
    type_entry["emitters"].extend([
        {"action": "propose-update", "on": "completion-verified"},
        {"action": "propose-update", "on": "silent-too-long"},
    ])

    # Create program that meets BOTH conditions:
    # 1. Terminal phase (triggers fact door)
    # 2. Silent 50+ days with live sentinel (triggers silent door)
    long_ago = (NOW.toordinal() - 50)
    old_date = date.fromordinal(long_ago).isoformat()

    program_id, _ = pl.create_program(
        type="roadmap-initiative",
        title="Terminal and dormant",
        owner_role="pm",
        frontmatter_extra={
            "phase": "verified",  # TERMINAL
            "phase_entered": {"verified": "2026-05-01"},
            "checkpoints": [],
        },
        root=root,
    )

    # Parseable old observation so the SILENT door genuinely fires too (not just
    # the fact/terminal door) -- only then does the op-dedupe get exercised.
    body = f"## Observations\n\n### {old_date}\nold observation"
    program = pl.read_program(program_id, root=root)
    fm = program["frontmatter"]
    pl._write_program_file(program["filepath"], fm, body)

    # Create telemetry with live sentinel
    import sentinel_runner
    sentinel_runs_path = sentinel_runner._sentinel_runs_path(root)
    os.makedirs(os.path.dirname(sentinel_runs_path), exist_ok=True)
    with open(sentinel_runs_path, "w") as f:
        json.dump({
            "movement-watch": {
                "last_run": NOW.isoformat(),
                "last_error": None,
            }
        }, f)

    # Call _evaluate_emitters
    program = pl.read_program(program_id, root=root)
    period = reconcile.current_period("weekly", NOW)
    emitted = reconcile._evaluate_emitters(
        program, type_entry, "holding", {}, body=body, root=root,
        period=period, registry=registry, now=NOW
    )

    # Should emit exactly ONE archive card (dedupe on op="archive")
    assert len(emitted) == 1
    task_id = emitted[0]

    # Verify it's an archive proposal
    card = task_lib.read_task(task_id)
    proposal = card["frontmatter"].get("proposal", {})
    assert proposal.get("op") == "archive"


# ─── fire_weekday emitter scheduling (day-of-week timing) ─────────────────────
#
# The `fire_weekday` field (1-7, ISO weekday Mon-Sun) gates emitter evaluation.
# When set, the emitter fires ONLY on that weekday; other days are skipped.


def _day_registry(fire_weekday=None):
    """A minimal registry with a draft-message emitter and optional fire_weekday."""
    emitter = {"on": "cycle-fresh", "action": "draft-message", "template": "nudge"}
    if fire_weekday is not None:
        emitter["fire_weekday"] = fire_weekday
    return {
        "types": [
            {
                "id": "weekly-priorities",
                "label": "Weekly priorities",
                "state_model": "cycle",
                "cadence": "weekly",
                "emitters": [emitter],
            }
        ]
    }


def test_fire_weekday_fires_on_matching_day(tmp_path):
    # NOW = 2026-06-16, which is a Tuesday (ISO weekday 2).
    # fire_weekday=2 -> should fire.
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    program = pl.read_program(program_id, root=root)
    result = reconcile.reconcile_program(
        program, _day_registry(fire_weekday=2), now=NOW, force=True
    )

    # The emitter fired: one send-message collab card created.
    assert len(result["emitted"]) == 1
    cards = task_lib.list_tasks(queue="collab", status="open")
    assert len(cards) == 1


def test_fire_weekday_skips_on_non_matching_day(tmp_path):
    # NOW = 2026-06-16 = Tuesday (ISO weekday 2).
    # fire_weekday=3 (Wednesday) -> should NOT fire.
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    program = pl.read_program(program_id, root=root)
    result = reconcile.reconcile_program(
        program, _day_registry(fire_weekday=3), now=NOW, force=True
    )

    # The emitter was skipped: no card created.
    assert result["emitted"] == []
    cards = task_lib.list_tasks(queue="collab", status="open")
    assert len(cards) == 0


def test_fire_weekday_monday_iso_1(tmp_path):
    # ISO weekday 1 = Monday. Test it works (regression for off-by-one bugs).
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    # Monday in the same week: 2026-06-15
    monday = date(2026, 6, 15)
    assert monday.isoweekday() == 1  # Verify it's Monday

    program = pl.read_program(program_id, root=root)
    result = reconcile.reconcile_program(
        program, _day_registry(fire_weekday=1), now=monday, force=True
    )

    # fire_weekday=1 on Monday -> should fire.
    assert len(result["emitted"]) == 1


def test_fire_weekday_sunday_iso_7(tmp_path):
    # ISO weekday 7 = Sunday. Test it works (regression for off-by-one bugs).
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    # Sunday in the same week: 2026-06-21
    sunday = date(2026, 6, 21)
    assert sunday.isoweekday() == 7  # Verify it's Sunday

    program = pl.read_program(program_id, root=root)
    result = reconcile.reconcile_program(
        program, _day_registry(fire_weekday=7), now=sunday, force=True
    )

    # fire_weekday=7 on Sunday -> should fire.
    assert len(result["emitted"]) == 1


def test_fire_weekday_absent_fires_every_day(tmp_path):
    # When fire_weekday is absent, emitter fires regardless of weekday.
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    program = pl.read_program(program_id, root=root)
    # No fire_weekday set -> should fire on any day.
    result = reconcile.reconcile_program(
        program, _day_registry(fire_weekday=None), now=NOW, force=True
    )

    assert len(result["emitted"]) == 1
    cards = task_lib.list_tasks(queue="collab", status="open")
    assert len(cards) == 1


def test_fire_weekday_invalid_value_treated_as_none(tmp_path):
    # If fire_weekday is not a valid int (or out of 1-7), it should not filter.
    # The validation gate (program_schema.py) rejects invalid values at save time,
    # but the emitter handler should gracefully skip invalid values at runtime.
    root = str(tmp_path / "data")
    program_id = _seed_holding_weekly_priorities(root)

    # Build a registry with invalid fire_weekday (bypasses schema validation).
    registry = {
        "types": [
            {
                "id": "weekly-priorities",
                "label": "Weekly priorities",
                "state_model": "cycle",
                "cadence": "weekly",
                "emitters": [
                    {
                        "on": "cycle-fresh",
                        "action": "draft-message",
                        "template": "nudge",
                        "fire_weekday": "invalid",  # Not an int
                    }
                ],
            }
        ]
    }

    program = pl.read_program(program_id, root=root)
    result = reconcile.reconcile_program(
        program, registry, now=NOW, force=True
    )

    # Invalid fire_weekday is treated as "not set" -> emitter fires.
    assert len(result["emitted"]) == 1
