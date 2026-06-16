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
