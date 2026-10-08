"""A fresh clone has no datasets/tasks/_counter (it's gitignored); the first
task create must seed it rather than crash with FileNotFoundError."""
import os

import task_lib


def _point_at(monkeypatch, tasks_dir):
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(task_lib, "ARCHIVE_DIR", str(tasks_dir / "_archive"))
    monkeypatch.setattr(task_lib, "COUNTER_FILE", str(tasks_dir / "_counter"))


def test_missing_counter_starts_at_one(tmp_path, monkeypatch):
    _point_at(monkeypatch, tmp_path / "tasks")
    assert task_lib._next_id() == "TASK-0001"
    assert task_lib._next_id() == "TASK-0002"


def test_missing_counter_resumes_past_existing_tasks(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks"
    (tasks / "human").mkdir(parents=True)
    (tasks / "_archive").mkdir()
    (tasks / "human" / "TASK-0007.md").write_text("x")
    (tasks / "_archive" / "TASK-0012.md").write_text("x")
    _point_at(monkeypatch, tasks)
    assert task_lib._next_id() == "TASK-0013"
    assert os.path.exists(tasks / "_counter")
