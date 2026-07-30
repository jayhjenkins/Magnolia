"""Tests for DispatchScheduler -- the safety-net sweep for stranded tasks.

Covers the core contract: tasks in dispatchable queues (agent/collab) that
were created but never dispatched get caught by the periodic sweep, while
tasks already dispatched or too young (staleness guard) are skipped.
"""
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import dispatch_scheduler as ds
from dispatch_scheduler import DispatchScheduler


def _make_task(task_id, agent_status=None, created=None, card_type=None,
               queue="agent", status="open"):
    if created is None:
        created = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    t = {
        "id": task_id,
        "title": f"Test task {task_id}",
        "queue": queue,
        "status": status,
        "priority": "medium",
        "created": created,
        "agent_status": agent_status,
    }
    if card_type:
        t["card_type"] = card_type
    return t


def test_sweep_dispatches_stale_undispatched_task(monkeypatch):
    """Tasks with agent_status None and age > staleness_threshold get dispatched."""
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(ds, "get_actionable_tasks",
                        lambda: [_make_task("TASK-9001", created=old)])

    dispatched = []
    scheduler = DispatchScheduler(
        staleness_threshold=90,
        dispatch_fn=lambda tid: dispatched.append(tid),
    )
    scheduler.tick()

    assert dispatched == ["TASK-9001"]


def test_sweep_skips_young_task(monkeypatch):
    """Tasks younger than staleness_threshold are NOT dispatched (double-dispatch guard)."""
    just_now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(ds, "get_actionable_tasks",
                        lambda: [_make_task("TASK-9002", created=just_now)])

    dispatched = []
    scheduler = DispatchScheduler(
        staleness_threshold=90,
        dispatch_fn=lambda tid: dispatched.append(tid),
    )
    scheduler.tick()

    assert dispatched == []


def test_sweep_skips_running_task(monkeypatch):
    """Tasks with agent_status 'running' are not dispatched."""
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(ds, "get_actionable_tasks",
                        lambda: [_make_task("TASK-9003", agent_status="running",
                                            created=old)])

    dispatched = []
    scheduler = DispatchScheduler(
        staleness_threshold=90,
        dispatch_fn=lambda tid: dispatched.append(tid),
    )
    scheduler.tick()

    assert dispatched == []


def test_sweep_skips_complete_task(monkeypatch):
    """Tasks with agent_status 'complete' are not dispatched."""
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(ds, "get_actionable_tasks",
                        lambda: [_make_task("TASK-9004", agent_status="complete",
                                            created=old)])

    dispatched = []
    scheduler = DispatchScheduler(
        staleness_threshold=90,
        dispatch_fn=lambda tid: dispatched.append(tid),
    )
    scheduler.tick()

    assert dispatched == []


def test_sweep_skips_empty_string_agent_status(monkeypatch):
    """Tasks with agent_status '' (rerun reset) are not dispatched by the sweep."""
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(ds, "get_actionable_tasks",
                        lambda: [_make_task("TASK-9005", agent_status="",
                                            created=old)])

    dispatched = []
    scheduler = DispatchScheduler(
        staleness_threshold=90,
        dispatch_fn=lambda tid: dispatched.append(tid),
    )
    scheduler.tick()

    assert dispatched == []


def test_sweep_dispatch_failure_continues(monkeypatch):
    """A failed dispatch for one task does not block subsequent tasks."""
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    monkeypatch.setattr(ds, "get_actionable_tasks",
                        lambda: [_make_task("TASK-9006", created=old),
                                 _make_task("TASK-9007", created=old)])

    dispatched = []

    def flaky_dispatch(tid):
        if tid == "TASK-9006":
            raise RuntimeError("simulated dispatch failure")
        dispatched.append(tid)

    scheduler = DispatchScheduler(
        staleness_threshold=90,
        dispatch_fn=flaky_dispatch,
    )
    scheduler.tick()

    assert "TASK-9007" in dispatched


def test_tick_is_exception_safe(monkeypatch):
    """An error from get_actionable_tasks does not crash the scheduler."""
    def boom():
        raise RuntimeError("simulated scan failure")

    monkeypatch.setattr(ds, "get_actionable_tasks", boom)

    dispatched = []
    scheduler = DispatchScheduler(
        staleness_threshold=90,
        dispatch_fn=lambda tid: dispatched.append(tid),
    )
    scheduler.tick()

    assert dispatched == []
