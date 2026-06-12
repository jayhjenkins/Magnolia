"""Worker dispatch filters by adaptation liveness.

load_workers() must skip a worker whose frontmatter carries
`adaptation: <id>` when that adaptation is not live, include it when live,
and always include untagged workers (which never appear in any manifest).
"""

import os
import textwrap

import task_dispatch


def _write_worker(workers_dir, slug, *, adaptation=None, priority=0):
    """Write a minimal valid worker .md into workers_dir; return its path."""
    lines = [
        "---",
        f"name: {slug}",
        f"description: Test worker {slug}",
        f"priority: {priority}",
    ]
    if adaptation is not None:
        lines.append(f"adaptation: {adaptation}")
    lines += ["---", "", f"Body for {slug}.", ""]
    path = os.path.join(workers_dir, f"{slug}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _names(workers):
    return {w.get("name") for w in workers}


def test_untagged_worker_always_included(tmp_path, monkeypatch):
    workers_dir = tmp_path / "workers"
    workers_dir.mkdir()
    _write_worker(str(workers_dir), "plain")

    # is_live should never even be consulted for an untagged worker, but if it
    # is, make it the pessimistic answer to prove the worker survives anyway.
    monkeypatch.setattr(task_dispatch, "WORKERS_DIR", str(workers_dir))
    monkeypatch.setattr(task_dispatch.adaptations_lib, "is_live", lambda surface, ref: False)

    assert "plain" in _names(task_dispatch.load_workers())


def test_tagged_worker_included_when_adaptation_live(tmp_path, monkeypatch):
    workers_dir = tmp_path / "workers"
    workers_dir.mkdir()
    _write_worker(str(workers_dir), "stock_sentinel", adaptation="watch-the-stock")

    monkeypatch.setattr(task_dispatch, "WORKERS_DIR", str(workers_dir))
    monkeypatch.setattr(task_dispatch.adaptations_lib, "is_live", lambda surface, ref: True)

    assert "stock_sentinel" in _names(task_dispatch.load_workers())


def test_tagged_worker_excluded_when_adaptation_not_live(tmp_path, monkeypatch):
    workers_dir = tmp_path / "workers"
    workers_dir.mkdir()
    _write_worker(str(workers_dir), "stock_sentinel", adaptation="watch-the-stock")
    _write_worker(str(workers_dir), "plain")

    monkeypatch.setattr(task_dispatch, "WORKERS_DIR", str(workers_dir))
    monkeypatch.setattr(task_dispatch.adaptations_lib, "is_live", lambda surface, ref: False)

    names = _names(task_dispatch.load_workers())
    assert "stock_sentinel" not in names
    assert "plain" in names  # untagged sibling still survives


def test_is_live_called_with_worker_surface_and_relpath(tmp_path, monkeypatch):
    """The filter must query is_live with surface 'worker' and the repo-relative
    path of the worker file (matching how the manifest stores a worker's ref)."""
    workers_dir = tmp_path / "workers"
    workers_dir.mkdir()
    path = _write_worker(str(workers_dir), "stock_sentinel", adaptation="watch-the-stock")

    calls = []

    def _fake_is_live(surface, ref):
        calls.append((surface, ref))
        return True

    monkeypatch.setattr(task_dispatch, "WORKERS_DIR", str(workers_dir))
    monkeypatch.setattr(task_dispatch.adaptations_lib, "is_live", _fake_is_live)

    task_dispatch.load_workers()

    expected_ref = os.path.relpath(path, task_dispatch.PM_OS_DIR)
    assert ("worker", expected_ref) in calls
