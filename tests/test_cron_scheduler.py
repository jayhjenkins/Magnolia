"""Tests for CronScheduler — the daemon that creates AND dispatches cron tasks.

Covers the most fundamental job in the system: cron fires → task created →
task dispatched to a worker. A gap here means the agent queue fills up with
cards nobody works.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cron_lib
from cron_scheduler import CronScheduler


def _write_jobs(tmp_path, jobs):
    jobs_file = tmp_path / "cron" / "jobs.json"
    jobs_file.parent.mkdir(parents=True, exist_ok=True)
    jobs_file.write_text(json.dumps(jobs))
    return str(jobs_file)


def _make_job(job_id="CRON-TEST", name="Test job", enabled=True,
              auto_dispatch=True, next_run=None, cron_expr="0 9 * * 1"):
    if next_run is None:
        next_run = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    return {
        "id": job_id,
        "name": name,
        "cron_expr": cron_expr,
        "cron_human": "test",
        "enabled": enabled,
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-01T00:00:00Z",
        "expires": None,
        "last_run": None,
        "next_run": next_run,
        "run_count": 0,
        "auto_dispatch": auto_dispatch,
        "task_template": {
            "title": f"Test task from {job_id}",
            "queue": "agent",
            "priority": "medium",
            "domain": "ops",
            "description": "Test description",
            "tags": ["cron", job_id],
        },
        "raw_input": "",
        "task_history": [],
    }


# ─── Core contract: tick creates a task AND dispatches it ──────────────────

def test_tick_creates_and_dispatches_due_job(tmp_path, monkeypatch):
    """The fundamental contract: a due cron job produces a task AND dispatches it."""
    job = _make_job()
    jobs_file = _write_jobs(tmp_path, [job])
    monkeypatch.setattr(cron_lib, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(cron_lib, "COUNTER_FILE",
                        str(tmp_path / "cron" / "_counter"))

    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "agent").mkdir(parents=True)
    (tasks_dir / "_counter").write_text("9000")
    import task_lib
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))

    dispatch_calls = []
    scheduler = CronScheduler(dispatch_fn=lambda tid: dispatch_calls.append(tid))
    scheduler.tick()

    assert len(dispatch_calls) == 1, f"Expected 1 dispatch, got {len(dispatch_calls)}"
    assert dispatch_calls[0].startswith("TASK-")


def test_tick_skips_dispatch_when_auto_dispatch_false(tmp_path, monkeypatch):
    """Jobs with auto_dispatch=false create the task but don't dispatch it."""
    job = _make_job(auto_dispatch=False)
    jobs_file = _write_jobs(tmp_path, [job])
    monkeypatch.setattr(cron_lib, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(cron_lib, "COUNTER_FILE",
                        str(tmp_path / "cron" / "_counter"))

    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "agent").mkdir(parents=True)
    (tasks_dir / "_counter").write_text("9000")
    import task_lib
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))

    dispatch_calls = []
    scheduler = CronScheduler(dispatch_fn=lambda tid: dispatch_calls.append(tid))
    scheduler.tick()

    assert len(dispatch_calls) == 0
    # But the task was still created
    created = list((tasks_dir / "agent").glob("TASK-*.md"))
    assert len(created) == 1


def test_tick_skips_not_yet_due_job(tmp_path, monkeypatch):
    """Jobs whose next_run is in the future are not executed."""
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    job = _make_job(next_run=future)
    jobs_file = _write_jobs(tmp_path, [job])
    monkeypatch.setattr(cron_lib, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(cron_lib, "COUNTER_FILE",
                        str(tmp_path / "cron" / "_counter"))

    dispatch_calls = []
    scheduler = CronScheduler(dispatch_fn=lambda tid: dispatch_calls.append(tid))
    scheduler.tick()

    assert len(dispatch_calls) == 0


def test_tick_skips_disabled_job(tmp_path, monkeypatch):
    """Disabled jobs are never executed."""
    job = _make_job(enabled=False)
    jobs_file = _write_jobs(tmp_path, [job])
    monkeypatch.setattr(cron_lib, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(cron_lib, "COUNTER_FILE",
                        str(tmp_path / "cron" / "_counter"))

    dispatch_calls = []
    scheduler = CronScheduler(dispatch_fn=lambda tid: dispatch_calls.append(tid))
    scheduler.tick()

    assert len(dispatch_calls) == 0


def test_dispatch_failure_does_not_fail_task_creation(tmp_path, monkeypatch):
    """If dispatch raises, the task is still created and the scheduler continues."""
    job = _make_job()
    jobs_file = _write_jobs(tmp_path, [job])
    monkeypatch.setattr(cron_lib, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(cron_lib, "COUNTER_FILE",
                        str(tmp_path / "cron" / "_counter"))

    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "agent").mkdir(parents=True)
    (tasks_dir / "_counter").write_text("9000")
    import task_lib
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))

    def exploding_dispatch(tid):
        raise RuntimeError("dispatch boom")

    scheduler = CronScheduler(dispatch_fn=exploding_dispatch)
    scheduler.tick()

    created = list((tasks_dir / "agent").glob("TASK-*.md"))
    assert len(created) == 1


def test_no_dispatch_fn_means_no_dispatch(tmp_path, monkeypatch):
    """When dispatch_fn is None (legacy), tasks are created but not dispatched."""
    job = _make_job()
    jobs_file = _write_jobs(tmp_path, [job])
    monkeypatch.setattr(cron_lib, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(cron_lib, "COUNTER_FILE",
                        str(tmp_path / "cron" / "_counter"))

    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "agent").mkdir(parents=True)
    (tasks_dir / "_counter").write_text("9000")
    import task_lib
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))

    scheduler = CronScheduler(dispatch_fn=None)
    scheduler.tick()

    created = list((tasks_dir / "agent").glob("TASK-*.md"))
    assert len(created) == 1


def test_multiple_due_jobs_all_dispatch(tmp_path, monkeypatch):
    """When multiple jobs are due, each gets its own task AND dispatch."""
    jobs = [
        _make_job(job_id="CRON-A", name="Job A"),
        _make_job(job_id="CRON-B", name="Job B"),
        _make_job(job_id="CRON-C", name="Job C"),
    ]
    jobs_file = _write_jobs(tmp_path, jobs)
    monkeypatch.setattr(cron_lib, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(cron_lib, "COUNTER_FILE",
                        str(tmp_path / "cron" / "_counter"))

    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "agent").mkdir(parents=True)
    (tasks_dir / "_counter").write_text("9000")
    import task_lib
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))

    dispatch_calls = []
    scheduler = CronScheduler(dispatch_fn=lambda tid: dispatch_calls.append(tid))
    scheduler.tick()

    assert len(dispatch_calls) == 3
    created = list((tasks_dir / "agent").glob("TASK-*.md"))
    assert len(created) == 3


# ─── Startup tick deduplication ────────────────────────────────────────────

def test_startup_tick_skips_very_overdue_jobs(tmp_path, monkeypatch):
    """On startup, jobs more than 1 hour overdue are skipped (prevents dupes on restart)."""
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    job = _make_job(next_run=old)
    jobs_file = _write_jobs(tmp_path, [job])
    monkeypatch.setattr(cron_lib, "JOBS_FILE", jobs_file)
    monkeypatch.setattr(cron_lib, "COUNTER_FILE",
                        str(tmp_path / "cron" / "_counter"))

    dispatch_calls = []
    scheduler = CronScheduler(dispatch_fn=lambda tid: dispatch_calls.append(tid))
    scheduler.tick(startup=True)

    assert len(dispatch_calls) == 0
