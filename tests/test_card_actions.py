"""Task 8 — card action backend: accept->apply->receipt->undo + graduate.

The git apply/commit/revert path runs with `git -C task_server.PM_OS_DIR`, so each
test monkeypatches PM_OS_DIR to a throwaway git repo. graduate_card writes through
ladder_lib via an explicit ladder_path so it never touches the real store.
"""
import subprocess


def _git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def test_accept_applies_patch_and_spawns_receipt(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(str(repo), "init")
    _git(str(repo), "config", "user.email", "t@e.com")
    _git(str(repo), "config", "user.name", "t")
    target = repo / "hello.txt"
    target.write_text("hello\n")
    _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "init")
    patch = repo / "p.patch"
    patch.write_text("--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-hello\n+hello world\n")
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(repo))
    tid, _ = task_lib.create_task("rec", queue="collab", card_type="recommendation", patch_path="p.patch")
    receipt_id = task_server.apply_recommendation(tid)
    assert (repo / "hello.txt").read_text() == "hello world\n"
    rc = task_lib.read_task(receipt_id)["frontmatter"]
    assert rc["card_type"] == "receipt"
    assert rc.get("revert_commit")


def test_accept_bad_patch_raises(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib, pytest
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(str(repo), "init")
    _git(str(repo), "config", "user.email", "t@e.com"); _git(str(repo), "config", "user.name", "t")
    (repo / "hello.txt").write_text("hello\n")
    _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "init")
    (repo / "bad.patch").write_text("--- a/nonexistent.txt\n+++ b/nonexistent.txt\n@@ -1 +1 @@\n-x\n+y\n")
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(repo))
    tid, _ = task_lib.create_task("rec", queue="collab", card_type="recommendation", patch_path="bad.patch")
    with pytest.raises(RuntimeError):
        task_server.apply_recommendation(tid)


def test_accept_no_patch_raises_valueerror(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib, pytest
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(tmp_path))
    tid, _ = task_lib.create_task("rec", queue="collab", card_type="recommendation")  # no patch_path
    with pytest.raises(ValueError):
        task_server.apply_recommendation(tid)


def test_undo_reverts_commit(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(str(repo), "init")
    _git(str(repo), "config", "user.email", "t@e.com"); _git(str(repo), "config", "user.name", "t")
    (repo / "hello.txt").write_text("hello\n")
    _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "init")
    (repo / "p.patch").write_text("--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-hello\n+hello world\n")
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(repo))
    tid, _ = task_lib.create_task("rec", queue="collab", card_type="recommendation", patch_path="p.patch")
    receipt_id = task_server.apply_recommendation(tid)
    task_server.undo_receipt(receipt_id)
    assert (repo / "hello.txt").read_text() == "hello\n"  # reverted


def test_graduate_advances_tier(tasks_root, tmp_path):
    import task_server, task_lib, ladder_lib
    p = str(tmp_path / "ladder.json")
    tid, _ = task_lib.create_task("grad", queue="collab", card_type="graduation")
    task_lib.update_task(tid, changes={"grad_task_type": "prd-draft", "grad_proposed_tier": "supervised"})
    task_server.graduate_card(tid, ladder_path=p)
    assert ladder_lib.tier_of("prd-draft", path=p) == "supervised"


def test_accepted_recommendation_is_archived(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib
    repo = tmp_path / "repo"; repo.mkdir()
    _git(str(repo), "init"); _git(str(repo), "config", "user.email", "t@e.com"); _git(str(repo), "config", "user.name", "t")
    (repo / "hello.txt").write_text("hello\n"); _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "init")
    (repo / "p.patch").write_text("--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-hello\n+hi\n")
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(repo))
    tid, _ = task_lib.create_task("rec", queue="collab", card_type="recommendation", patch_path="p.patch")
    task_server.apply_recommendation(tid)
    active_ids = [t["id"] for t in task_lib.list_tasks()]
    assert tid not in active_ids  # recommendation archived, no longer on the board


def test_graduated_card_is_archived(tasks_root, tmp_path):
    import task_server, task_lib, ladder_lib
    p = str(tmp_path / "ladder.json")
    tid, _ = task_lib.create_task("grad", queue="collab", card_type="graduation")
    task_lib.update_task(tid, changes={"grad_task_type": "prd-draft", "grad_proposed_tier": "supervised"})
    task_server.graduate_card(tid, ladder_path=p)
    assert tid not in [t["id"] for t in task_lib.list_tasks()]


def test_accept_empty_patch_raises_and_rolls_back(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib, pytest
    repo = tmp_path / "repo"; repo.mkdir()
    _git(str(repo), "init"); _git(str(repo), "config", "user.email", "t@e.com"); _git(str(repo), "config", "user.name", "t")
    (repo / ".gitignore").write_text("ignored.txt\n")
    (repo / "hello.txt").write_text("hello\n"); _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "init")
    # A patch that only creates a gitignored file -> nothing stageable. The patch
    # file lives OUTSIDE the repo (absolute patch_path) so `git add -A` does not pick
    # up the patch file itself; after apply, `git diff --cached --quiet` sees nothing
    # staged -> the "no committable changes" path fires.
    patch = tmp_path / "p.patch"
    patch.write_text("--- /dev/null\n+++ b/ignored.txt\n@@ -0,0 +1 @@\n+x\n")
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(repo))
    tid, _ = task_lib.create_task("rec", queue="collab", card_type="recommendation", patch_path=str(patch))
    with pytest.raises(RuntimeError):
        task_server.apply_recommendation(tid)
    # tree restored: no stray ignored.txt content committed, repo clean of tracked changes
    status = _git(str(repo), "status", "--porcelain").stdout
    assert "hello.txt" not in status  # tracked files untouched


# ─── Cadence proposal accept: applies a LOCAL program mutation, no git ────────
# A cadence-propose-update recommendation card carries a `proposal` mutation and
# is tagged [program_id, "cadence"]. Accept branches BEFORE the git-patch path:
# it applies the mutation via program_lib.apply_mutation (a working-tree program
# file write, NOT a git commit), completes the card, and spawns an informational
# receipt (no revert_commit — a program mutation is not a git revert).

def _seed_cadence_program(tmp_path, monkeypatch):
    """Seed an isolated roadmap-initiative program in discovery and return its id."""
    import program_lib
    pdir = tmp_path / "datasets" / "programs"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(program_lib, "_program_dir", lambda root=None: str(pdir))
    monkeypatch.setattr(
        program_lib, "_counter_path", lambda root=None: str(pdir / "_counter"))
    pid, _ = program_lib.create_program(
        type="roadmap-initiative", title="Payments revamp", owner_role="product",
        intent="Seed intent.", root=str(tmp_path),
        frontmatter_extra={
            "phase": "discovery",
            "phase_entered": {"discovery": "2026-05-01"},
            "checkpoints": [
                {"id": "discovery-exit", "label": "Discovery exit",
                 "due": "2026-05-19", "instrument": "human attestation",
                 "status": "pending"},
            ],
        })
    return pid


def test_accept_cadence_proposal_mutates_program_no_git(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib, program_lib
    pid = _seed_cadence_program(tmp_path, monkeypatch)
    # PM_OS_DIR points at a NON-repo dir: if the accept took the git path it would
    # blow up, proving the cadence branch never shells out to git.
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(tmp_path / "not-a-repo"))
    proposal = {"op": "advance-phase", "to": "planning",
                "checkpoint": "discovery-exit", "from": "discovery"}
    tid, _ = task_lib.create_task(
        "advance?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", tags=[pid, "cadence"],
        proposal=proposal)
    receipt_id = task_server.apply_recommendation(tid)
    # program advanced via the local file write
    fm = program_lib.read_program(pid, root=str(tmp_path))["frontmatter"]
    assert fm["phase"] == "planning"
    # proposal card archived
    assert tid not in [t["id"] for t in task_lib.list_tasks()]
    # receipt spawned, informational: NO revert_commit (not a git revert)
    rc = task_lib.read_task(receipt_id)["frontmatter"]
    assert rc["card_type"] == "receipt"
    assert not rc.get("revert_commit")


def test_accept_cadence_proposal_makes_no_git_commit(tasks_root, tmp_path, monkeypatch):
    """The cadence path must not git apply/commit even when PM_OS_DIR IS a repo."""
    import task_server, task_lib
    repo = tmp_path / "repo"; repo.mkdir()
    _git(str(repo), "init"); _git(str(repo), "config", "user.email", "t@e.com")
    _git(str(repo), "config", "user.name", "t")
    (repo / "hello.txt").write_text("hello\n")
    _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "init")
    head_before = _git(str(repo), "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(repo))
    pid = _seed_cadence_program(tmp_path, monkeypatch)
    tid, _ = task_lib.create_task(
        "advance?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", tags=[pid, "cadence"],
        proposal={"op": "advance-phase", "to": "planning"})
    task_server.apply_recommendation(tid)
    head_after = _git(str(repo), "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before  # no commit landed


def test_accept_normal_recommendation_still_git_applies(tasks_root, tmp_path, monkeypatch):
    """Regression: a non-cadence recommendation card still takes the git path."""
    import task_server, task_lib
    repo = tmp_path / "repo"; repo.mkdir()
    _git(str(repo), "init"); _git(str(repo), "config", "user.email", "t@e.com")
    _git(str(repo), "config", "user.name", "t")
    (repo / "hello.txt").write_text("hello\n")
    _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "init")
    (repo / "p.patch").write_text(
        "--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-hello\n+hello world\n")
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(repo))
    tid, _ = task_lib.create_task("rec", queue="collab",
                                  card_type="recommendation", patch_path="p.patch")
    receipt_id = task_server.apply_recommendation(tid)
    assert (repo / "hello.txt").read_text() == "hello world\n"
    rc = task_lib.read_task(receipt_id)["frontmatter"]
    assert rc.get("revert_commit")  # git path: receipt records the revert commit


def test_cadence_propose_update_defaults_to_shadow(tmp_path):
    """The proposal action-type rides the ladder at shadow (propose-only) by default."""
    import ladder_lib
    p = str(tmp_path / "ladder.json")
    assert ladder_lib.tier_of("cadence-propose-update", path=p) == "shadow"


def test_undo_conflict_aborts_cleanly(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib, pytest
    repo = tmp_path / "repo"; repo.mkdir()
    _git(str(repo), "init"); _git(str(repo), "config", "user.email", "t@e.com"); _git(str(repo), "config", "user.name", "t")
    (repo / "hello.txt").write_text("line1\n"); _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "init")
    (repo / "p.patch").write_text("--- a/hello.txt\n+++ b/hello.txt\n@@ -1 +1 @@\n-line1\n+line2\n")
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(repo))
    tid, _ = task_lib.create_task("rec", queue="collab", card_type="recommendation", patch_path="p.patch")
    receipt_id = task_server.apply_recommendation(tid)
    # now make a conflicting change on top so the revert can't apply cleanly
    (repo / "hello.txt").write_text("line2-edited\n"); _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "edit")
    with pytest.raises(RuntimeError):
        task_server.undo_receipt(receipt_id)
    # tree must NOT be stuck mid-revert
    import os
    assert not os.path.exists(str(repo / ".git" / "REVERT_HEAD"))
    status = _git(str(repo), "status", "--porcelain").stdout
    assert "UU" not in status and "<<<<<<<" not in (repo / "hello.txt").read_text()
