#!/usr/bin/env python3
"""
test_cadence_scheduler.py - Unit tests for the CadenceScheduler daemon (Task 5).

These tests NEVER start a real daemon thread and NEVER sleep. They call tick()
directly and monkeypatch reconcile.reconcile_all, mirroring how the cron path is
exercised. The task_server.py wiring is verified by import-ability + the live
e2e (a later controller step), not here.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from cadence.scheduler import CadenceScheduler
from cadence import reconcile


def test_tick_calls_reconcile_all_once(monkeypatch):
    """tick() invokes reconcile.reconcile_all exactly once."""
    calls = []

    def fake_reconcile_all(*args, **kwargs):
        calls.append((args, kwargs))
        return [
            {"program_id": "PROG-0001", "verdict": "broken", "new_cycle": True, "emitted": ["TASK-0001"]},
            {"program_id": "PROG-0002", "verdict": "holding", "new_cycle": False, "emitted": []},
        ]

    monkeypatch.setattr(reconcile, "reconcile_all", fake_reconcile_all)

    scheduler = CadenceScheduler()
    scheduler.tick()

    assert len(calls) == 1


def test_tick_swallows_reconcile_errors(monkeypatch):
    """A reconcile_all that raises must NOT propagate out of tick()."""
    def boom(*args, **kwargs):
        raise RuntimeError("reconcile blew up")

    monkeypatch.setattr(reconcile, "reconcile_all", boom)

    scheduler = CadenceScheduler()
    # Must not raise.
    scheduler.tick()

    # State stays usable: a subsequent good tick still runs.
    calls = []
    monkeypatch.setattr(reconcile, "reconcile_all", lambda *a, **k: calls.append(1) or [])
    scheduler.tick()
    assert len(calls) == 1


def test_double_start_guard(monkeypatch):
    """start() twice does not spawn a second thread; stop() cleans up."""
    # Keep tick() inert so the daemon's startup tick does no real work.
    monkeypatch.setattr(reconcile, "reconcile_all", lambda *a, **k: [])

    scheduler = CadenceScheduler()
    try:
        scheduler.start()
        first_thread = scheduler._thread
        scheduler.start()  # guarded - same thread, no second spawn
        assert scheduler._thread is first_thread
    finally:
        scheduler.stop()
    assert scheduler._running is False


# ─── Sentinel scheduling ─────────────────────────────────────────────────────

from cadence.scheduler import _should_run_sentinels, _is_workday, SENTINEL_HOURS
from datetime import datetime, timezone


def test_should_run_sentinels_on_workday_at_sentinel_hour():
    tue_9am = datetime(2026, 7, 28, 9, 15, tzinfo=timezone.utc)
    assert _is_workday(tue_9am)
    assert _should_run_sentinels(tue_9am, (None, None))


def test_should_not_run_sentinels_on_weekend():
    sat_9am = datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc)
    assert not _is_workday(sat_9am)
    assert not _should_run_sentinels(sat_9am, (None, None))


def test_should_not_run_sentinels_at_wrong_hour():
    tue_11am = datetime(2026, 7, 28, 11, 0, tzinfo=timezone.utc)
    assert _is_workday(tue_11am)
    assert not _should_run_sentinels(tue_11am, (None, None))


def test_should_not_run_sentinels_twice_same_hour():
    tue_9am = datetime(2026, 7, 28, 9, 30, tzinfo=timezone.utc)
    already = ("2026-07-28", 9)
    assert not _should_run_sentinels(tue_9am, already)


def test_tick_dispatches_sentinels_at_sentinel_hour(monkeypatch):
    dispatched = []
    monkeypatch.setattr(reconcile, "reconcile_all", lambda *a, **k: [])
    from cadence import scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "_dispatch_sentinel", lambda name: dispatched.append(name))
    tue_9am = datetime(2026, 7, 28, 9, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(sched_mod, "datetime", type("FakeDT", (), {
        "now": staticmethod(lambda tz=None: tue_9am),
    }))

    scheduler = CadenceScheduler()
    scheduler.tick()

    assert "movement-watch" in dispatched
    assert "tracker-truth" in dispatched


def test_tick_skips_sentinels_at_non_sentinel_hour(monkeypatch):
    dispatched = []
    monkeypatch.setattr(reconcile, "reconcile_all", lambda *a, **k: [])
    from cadence import scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "_dispatch_sentinel", lambda name: dispatched.append(name))
    tue_11am = datetime(2026, 7, 28, 11, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sched_mod, "datetime", type("FakeDT", (), {
        "now": staticmethod(lambda tz=None: tue_11am),
    }))

    scheduler = CadenceScheduler()
    scheduler.tick()

    assert dispatched == []
