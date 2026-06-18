"""Phase 9 PR1 — factory_lib: precise staging, receipt emission, worker validation.

Git ops run with git -C factory_lib.PM_OS_DIR; the round-trip Undo test also points
task_server.PM_OS_DIR at the same throwaway repo. Uses the tasks_root fixture so
receipt cards write to a throwaway task tree.
"""
import subprocess
import pytest


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(str(repo), "init")
    _git(str(repo), "config", "user.email", "t@e.com")
    _git(str(repo), "config", "user.name", "t")
    (repo / "seed.txt").write_text("seed\n")
    _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "init")
    return repo


def test_commit_emits_receipt_with_revert_commit(tasks_root, tmp_path, monkeypatch):
    import factory_lib, task_lib
    repo = _repo(tmp_path)
    (repo / "new.md").write_text("scaffolded\n")
    monkeypatch.setattr(factory_lib, "PM_OS_DIR", str(repo))
    rid = factory_lib.commit_and_emit_receipt(
        "a sample worker", files=["new.md"], kind="worker")
    fm = task_lib.read_task(rid)["frontmatter"]
    assert fm["card_type"] == "receipt"
    assert fm.get("revert_commit")
    assert fm.get("receipt_summary") == "a sample worker"
    assert fm.get("factory_kind") == "worker"


def test_staging_is_precise_not_add_all(tasks_root, tmp_path, monkeypatch):
    """Only the named file is committed; an unrelated dirty file stays uncommitted."""
    import factory_lib
    repo = _repo(tmp_path)
    (repo / "new.md").write_text("scaffolded\n")
    (repo / "unrelated.txt").write_text("do not commit me\n")
    monkeypatch.setattr(factory_lib, "PM_OS_DIR", str(repo))
    factory_lib.commit_and_emit_receipt("x", files=["new.md"], kind="worker")
    # unrelated.txt must still be untracked (not swept into the factory commit)
    status = _git(str(repo), "status", "--porcelain").stdout
    assert "unrelated.txt" in status


def test_empty_scaffold_raises(tasks_root, tmp_path, monkeypatch):
    import factory_lib
    repo = _repo(tmp_path)
    monkeypatch.setattr(factory_lib, "PM_OS_DIR", str(repo))
    with pytest.raises(factory_lib.FactoryError):
        factory_lib.commit_and_emit_receipt("x", files=["seed.txt"], kind="worker")  # unchanged


def test_undo_reverts_factory_commit(tasks_root, tmp_path, monkeypatch):
    import factory_lib, task_server
    repo = _repo(tmp_path)
    (repo / "new.md").write_text("scaffolded\n")
    monkeypatch.setattr(factory_lib, "PM_OS_DIR", str(repo))
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(repo))
    rid = factory_lib.commit_and_emit_receipt("x", files=["new.md"], kind="worker")
    assert (repo / "new.md").exists()
    task_server.undo_receipt(rid)
    assert not (repo / "new.md").exists()  # revert removed the added file


def test_validate_worker_accepts_good_and_flags_missing_fields(tmp_path):
    import factory_lib
    good = tmp_path / "good.md"
    good.write_text(
        "---\nname: sample\ndescription: Use when x\npriority: 10\ntier: standard\n"
        "match:\n  task_type: []\nallowed_tools:\n  - \"Bash(*)\"\nskills: []\n"
        "timeout: 300\nmax_turns: 15\n---\n\nbody\n")
    assert factory_lib.validate_worker(str(good)) == []
    bad = tmp_path / "bad.md"
    bad.write_text("---\nname: sample\ndescription: Use when x\n---\n\nbody\n")
    problems = factory_lib.validate_worker(str(bad))
    missing = {p.split(": ", 1)[1] for p in problems if p.startswith("missing required field")}
    assert missing == {"priority", "tier", "match", "allowed_tools", "timeout", "max_turns"}


def test_validate_program_type_accepts_real_and_flags_missing():
    import factory_lib
    # the shipped registry validates and contains a known type
    assert factory_lib.validate_program_type("weekly-priorities") == []
    # a type id not present is flagged (the factory's must-be-green check)
    errs = factory_lib.validate_program_type("no-such-type")
    assert any("no-such-type" in e for e in errs)


def test_validate_worker_flags_empty_body(tmp_path):
    import factory_lib
    nobody = tmp_path / "nobody.md"
    nobody.write_text(
        "---\nname: s\ndescription: Use when x\npriority: 10\ntier: standard\n"
        "match:\n  task_type: []\nallowed_tools:\n  - \"Bash(*)\"\ntimeout: 300\nmax_turns: 15\n---\n\n   \n")
    assert "empty prompt body" in factory_lib.validate_worker(str(nobody))


def test_receipt_failure_rolls_back_commit(tasks_root, tmp_path, monkeypatch):
    """If the receipt can't be emitted, the commit is rolled back (no orphan commit);
    the scaffolded files stay in the working tree for a retry."""
    import factory_lib, task_lib
    repo = _repo(tmp_path)
    (repo / "new.md").write_text("scaffolded\n")
    monkeypatch.setattr(factory_lib, "PM_OS_DIR", str(repo))
    head_before = _git(str(repo), "rev-parse", "HEAD").stdout.strip()
    def boom(*a, **k):
        raise RuntimeError("simulated receipt failure (e.g. missing counter)")
    monkeypatch.setattr(task_lib, "create_task", boom)
    with pytest.raises(factory_lib.FactoryError):
        factory_lib.commit_and_emit_receipt("x", files=["new.md"], kind="worker")
    head_after = _git(str(repo), "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before        # no orphan commit
    assert (repo / "new.md").exists()        # scaffolded file preserved in working tree
