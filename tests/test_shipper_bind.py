"""Tests for _maybe_bind_tracker in shipper.py."""
import program_lib
import task_lib
import shipper


def _pin_programs(tmp_path, monkeypatch):
    pdir = tmp_path / "datasets" / "programs"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(program_lib, "_program_dir", lambda root=None: str(pdir))
    monkeypatch.setattr(
        program_lib, "_counter_path", lambda root=None: str(pdir / "_counter"))


def test_publish_binds_tracker_to_tagged_program(tmp_path, tasks_root, monkeypatch):
    _pin_programs(tmp_path, monkeypatch)
    pid, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product")

    tid, _ = task_lib.create_task(
        f"Create tracker for {pid}", queue="agent",
        creator="cadence", tags=[pid, "cadence"])

    shipper._maybe_bind_tracker(tid, "VNT-99999")

    fm = program_lib.read_program(pid)["frontmatter"]
    bindings = fm.get("bindings") or []
    assert any(
        b.get("anchor") == "VNT-99999"
        and b.get("role") == "truth"
        for b in bindings
    )


def test_bind_tracker_idempotent(tmp_path, tasks_root, monkeypatch):
    _pin_programs(tmp_path, monkeypatch)
    pid, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product")

    tid, _ = task_lib.create_task(
        f"Create tracker for {pid}", queue="agent",
        creator="cadence", tags=[pid, "cadence"])

    shipper._maybe_bind_tracker(tid, "VNT-99999")
    shipper._maybe_bind_tracker(tid, "VNT-99999")

    fm = program_lib.read_program(pid)["frontmatter"]
    bindings = [b for b in (fm.get("bindings") or [])
                if b.get("kind") == "project_management"]
    assert len(bindings) == 1


def test_bind_tracker_skips_untagged_task(tmp_path, tasks_root, monkeypatch):
    _pin_programs(tmp_path, monkeypatch)
    tid, _ = task_lib.create_task(
        "Some untagged task", queue="agent", creator="human")

    shipper._maybe_bind_tracker(tid, "VNT-99999")
    # No crash, no side effects


def test_bind_tracker_best_effort_on_missing_program(tmp_path, tasks_root, monkeypatch):
    _pin_programs(tmp_path, monkeypatch)
    tid, _ = task_lib.create_task(
        "Tagged with nonexistent program", queue="agent",
        creator="cadence", tags=["PROG-9999", "cadence"])

    shipper._maybe_bind_tracker(tid, "VNT-99999")
    # No crash -- best-effort silently fails
