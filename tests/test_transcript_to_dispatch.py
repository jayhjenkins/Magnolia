"""Tests for the transcript-to-dispatch pipeline — the core use case.

The loop: meeting happens -> transcript synced -> tasks extracted -> tasks
dispatched to workers. If any link breaks, the system is useless.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import task_lib
import task_extract_meetings as tem


# ─── _dispatch_new_tasks: the last-mile handoff ───────────────────────────

def test_dispatch_new_tasks_dispatches_new_agent_tasks(tmp_path, monkeypatch):
    """Tasks created during extraction that land in agent/collab get dispatched."""
    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "agent").mkdir(parents=True)
    (tasks_dir / "_counter").write_text("100")
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(tem, "PM_OS_DIR", tmp_path)

    before_ids = set()

    task_lib.create_task("Do the thing", queue="agent", priority="high")
    task_lib.create_task("Do another thing", queue="agent", priority="medium")

    dispatch_calls = []
    real_popen = __import__("subprocess").Popen

    def fake_popen(cmd, **kwargs):
        for i, arg in enumerate(cmd):
            if arg == "--task" and i + 1 < len(cmd):
                dispatch_calls.append(cmd[i + 1])
        return real_popen(
            [sys.executable, "-c", "pass"],
            **{k: v for k, v in kwargs.items() if k != "env"},
        )

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    dispatched = tem._dispatch_new_tasks(before_ids)

    assert dispatched == 2
    assert len(dispatch_calls) == 2


def test_dispatch_new_tasks_skips_tasks_that_existed_before(tmp_path, monkeypatch):
    """Tasks that existed before extraction are NOT dispatched again."""
    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "agent").mkdir(parents=True)
    (tasks_dir / "_counter").write_text("100")
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(tem, "PM_OS_DIR", tmp_path)

    tid1, _ = task_lib.create_task("Old task", queue="agent")
    before_ids = {tid1}

    task_lib.create_task("New task", queue="agent")

    dispatch_calls = []
    real_popen = __import__("subprocess").Popen

    def fake_popen(cmd, **kwargs):
        for i, arg in enumerate(cmd):
            if arg == "--task" and i + 1 < len(cmd):
                dispatch_calls.append(cmd[i + 1])
        return real_popen(
            [sys.executable, "-c", "pass"],
            **{k: v for k, v in kwargs.items() if k != "env"},
        )

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    dispatched = tem._dispatch_new_tasks(before_ids)

    assert dispatched == 1
    assert tid1 not in dispatch_calls


def test_dispatch_new_tasks_skips_human_queue(tmp_path, monkeypatch):
    """Tasks in the human queue are not auto-dispatched."""
    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "human").mkdir(parents=True)
    (tasks_dir / "_counter").write_text("100")
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(tem, "PM_OS_DIR", tmp_path)

    task_lib.create_task("Human decision needed", queue="human")

    dispatch_calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: None)

    dispatched = tem._dispatch_new_tasks(set())
    assert dispatched == 0


def test_dispatch_failure_does_not_crash_extraction(tmp_path, monkeypatch):
    """If dispatch fails, extraction still completes."""
    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "agent").mkdir(parents=True)
    (tasks_dir / "_counter").write_text("100")
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(tem, "PM_OS_DIR", tmp_path)

    task_lib.create_task("Task that will fail dispatch", queue="agent")

    def exploding_popen(*a, **kw):
        raise RuntimeError("dispatch boom")

    monkeypatch.setattr("subprocess.Popen", exploding_popen)

    dispatched = tem._dispatch_new_tasks(set())
    assert dispatched == 0


# ─── _snapshot_task_ids: baseline before extraction ────────────────────────

def test_snapshot_captures_existing_task_ids(tmp_path, monkeypatch):
    """Snapshot returns IDs of tasks currently in dispatchable queues."""
    tasks_dir = tmp_path / "tasks"
    (tasks_dir / "agent").mkdir(parents=True)
    (tasks_dir / "collab").mkdir(parents=True)
    (tasks_dir / "human").mkdir(parents=True)
    (tasks_dir / "_counter").write_text("100")
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))

    tid_a, _ = task_lib.create_task("Agent task", queue="agent")
    tid_c, _ = task_lib.create_task("Collab task", queue="collab")
    tid_h, _ = task_lib.create_task("Human task", queue="human")

    ids = tem._snapshot_task_ids()

    assert tid_a in ids
    assert tid_c in ids
    assert tid_h not in ids


# ─── Integration: transcript_post -> extract -> dispatch ──────────────────

def test_transcript_post_fires_task_extract(tmp_path, monkeypatch):
    """transcript_post.run_downstream fires task_extract_meetings as a subprocess."""
    import transcript_post
    import profile_lib

    monkeypatch.setattr(profile_lib, "PM_OS_DIR", str(tmp_path))
    (tmp_path / "logs").mkdir()

    popen_cmds = []
    real_popen = __import__("subprocess").Popen

    class SpyPopen:
        def __init__(self, cmd, **kwargs):
            popen_cmds.append(cmd)

    monkeypatch.setattr("subprocess.Popen", SpyPopen)

    txt = tmp_path / "test.txt"
    txt.write_text("Test transcript content")

    state = {}

    def mock_classify(*a, **kw):
        raise ImportError("skip classify in test")

    monkeypatch.setattr(transcript_post, "_classify_fn", mock_classify)

    transcript_post.run_downstream(str(txt), "test-id", state,
                                   __import__("logging").getLogger())

    extract_cmds = [c for c in popen_cmds if "task_extract" in str(c)]
    assert len(extract_cmds) >= 1, f"Expected task_extract in spawned cmds, got: {popen_cmds}"
