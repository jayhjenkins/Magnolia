"""Tests for the pure verdict core of the Cadence reconciler (Task 1).

These cover ONLY the no-I/O helpers: current_period, _parse_iso_date, and
compute_verdict across all four state models. Program dicts are built inline in
the read_program shape ({"frontmatter": {...}, "body": ""}); the registry comes
from the real program_lib.load_registry().
"""

from datetime import date, datetime

import program_lib as pl
from cadence import reconcile


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
