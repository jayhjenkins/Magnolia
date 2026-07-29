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


def test_accept_cadence_proposal_emits_jira_sync_when_tracker_bound(
        tasks_root, tmp_path, monkeypatch):
    """Accepting a proposal on a program with a project_management binding
    emits a ticket-creator agent task to draft a Jira update."""
    import task_server, task_lib, program_lib
    pid = _seed_cadence_program(tmp_path, monkeypatch)
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(tmp_path / "not-a-repo"))
    monkeypatch.setattr(task_server, "_dispatch_bootstrap_task", lambda tid: None)
    prog = program_lib.read_program(pid, root=str(tmp_path))
    fm = prog["frontmatter"]
    fm["bindings"] = [{"id": "tracker", "role": "truth",
                       "kind": "project_management", "anchor": "VNT-42411",
                       "mode": "read", "health": "ok"}]
    program_lib._write_program_file(prog["filepath"], fm, prog["body"])
    proposal = {"op": "advance-phase", "to": "planning",
                "checkpoint": "discovery-exit", "from": "discovery"}
    tid, _ = task_lib.create_task(
        "advance?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", tags=[pid, "cadence"],
        proposal=proposal)
    task_server.apply_recommendation(tid)
    agent_tasks = [t for t in task_lib.list_tasks(queue="agent")
                   if "VNT-42411" in t.get("title", "")]
    assert len(agent_tasks) == 1
    assert agent_tasks[0].get("task_type") == "ticket-creator"


def test_accept_cadence_proposal_no_jira_sync_without_binding(
        tasks_root, tmp_path, monkeypatch):
    """No Jira sync task when the program has no project_management binding."""
    import task_server, task_lib
    pid = _seed_cadence_program(tmp_path, monkeypatch)
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(tmp_path / "not-a-repo"))
    proposal = {"op": "advance-phase", "to": "planning",
                "checkpoint": "discovery-exit", "from": "discovery"}
    tid, _ = task_lib.create_task(
        "advance?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", tags=[pid, "cadence"],
        proposal=proposal)
    task_server.apply_recommendation(tid)
    agent_tasks = [t for t in task_lib.list_tasks(queue="agent")
                   if "Jira" in t.get("title", "") or "VNT" in t.get("title", "")]
    assert len(agent_tasks) == 0


def test_cadence_propose_update_defaults_to_shadow(tmp_path):
    """The proposal action-type rides the ladder at shadow (propose-only) by default."""
    import ladder_lib
    p = str(tmp_path / "ladder.json")
    assert ladder_lib.tier_of("cadence-propose-update", path=p) == "shadow"


# ─── Birth proposal accept (inc4a): create program + enqueue bootstrap ────────
# A birth proposal is a cadence-propose-update recommendation carrying
# proposal {op: "birth", program_type, title, candidate_id, checkpoints, citations}
# tagged [intake_program_id, "cadence"]. Accept branches BEFORE apply_mutation:
# it births a new active program (program_lib.birth_program), marks the candidate
# birthed + linked, enqueues the born type's bootstrap_emissions as ordinary
# tasks, completes the card, and spawns a cadence-apply receipt. Tier-1: no git
# commit, no external write (bootstrap tasks are queued only).

def _seed_intake_with_candidate(tmp_path, monkeypatch):
    """Seed an isolated program-intake nursery holding one open candidate.

    Returns (intake_program_id, candidate_id). Uses the same dir/counter patches
    the cadence-proposal tests use so nothing leaks to the real program store.
    """
    import program_lib
    pdir = tmp_path / "datasets" / "programs"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(program_lib, "_program_dir", lambda root=None: str(pdir))
    monkeypatch.setattr(
        program_lib, "_counter_path", lambda root=None: str(pdir / "_counter"))
    intake_id, _ = program_lib.create_program(
        type="program-intake", title="Program intake", owner_role="product",
        intent="The nursery.", root=str(tmp_path),
        frontmatter_extra={"items": [], "status_line": "holding"})
    res = program_lib.upsert_candidate(
        intake_id, candidate_key="payments-revamp",
        program_type="roadmap-initiative", title="Payments revamp",
        source="meeting:2026-06-01", claim="Kickoff named.", root=str(tmp_path))
    return intake_id, res["candidate_id"]


def _birth_proposal(candidate_id):
    return {
        "op": "birth",
        "program_type": "roadmap-initiative",
        "title": "Payments revamp",
        "candidate_id": candidate_id,
        "checkpoints": [
            {"id": "discovery-exit", "label": "Discovery exit",
             "due": "2026-07-01", "instrument": "human attestation",
             "status": "met"},
        ],
        "citations": ["meeting:2026-06-01", "meeting:2026-06-08"],
    }


def test_accept_birth_proposal_creates_active_program(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib, program_lib
    intake_id, cand_id = _seed_intake_with_candidate(tmp_path, monkeypatch)
    # PM_OS_DIR points at a NON-repo dir: a git path would blow up, proving birth
    # never shells out to git.
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(tmp_path / "not-a-repo"))
    tid, _ = task_lib.create_task(
        "Birth roadmap-initiative: Payments revamp?", queue="human",
        card_type="recommendation", task_type="cadence-propose-update",
        tags=[intake_id, "cadence"], proposal=_birth_proposal(cand_id))
    receipt_id = task_server.apply_recommendation(tid)
    # A new active program was born. Its id is the receipt's program_id.
    rc = task_lib.read_task(receipt_id)["frontmatter"]
    new_id = rc.get("program_id")
    assert new_id and new_id != intake_id
    born = program_lib.read_program(new_id, root=str(tmp_path))["frontmatter"]
    assert born["status"] == "active"
    assert born["type"] == "roadmap-initiative"
    assert born["phase"] == "discovery"  # inferred first phase of the pipeline
    # the carried checkpoint is forced pending (a newborn has met nothing)
    cps = born.get("checkpoints") or []
    assert cps and cps[0]["status"] == "pending"
    # an origin observation is present
    body = program_lib.read_program(new_id, root=str(tmp_path))["body"]
    assert "program-intake" in body
    # proposal card archived; receipt informational (no git revert)
    assert tid not in [t["id"] for t in task_lib.list_tasks()]
    assert rc["card_type"] == "receipt"
    assert not rc.get("revert_commit")
    assert rc.get("receipt_kind") == "cadence-apply"


def test_accept_birth_marks_candidate_birthed(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib, program_lib
    intake_id, cand_id = _seed_intake_with_candidate(tmp_path, monkeypatch)
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(tmp_path / "not-a-repo"))
    tid, _ = task_lib.create_task(
        "Birth?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", tags=[intake_id, "cadence"],
        proposal=_birth_proposal(cand_id))
    receipt_id = task_server.apply_recommendation(tid)
    new_id = task_lib.read_task(receipt_id)["frontmatter"]["program_id"]
    fm = program_lib.read_program(intake_id, root=str(tmp_path))["frontmatter"]
    cand = next(c for c in fm["items"] if c["id"] == cand_id)
    assert cand["status"] == "birthed"
    assert cand["born_program_id"] == new_id


def test_accept_birth_enqueues_bootstrap_emissions(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib, program_lib
    intake_id, cand_id = _seed_intake_with_candidate(tmp_path, monkeypatch)
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(tmp_path / "not-a-repo"))
    # do not actually spawn a dispatcher process under test
    monkeypatch.setattr(task_server, "_dispatch_bootstrap_task", lambda tid: None)
    tid, _ = task_lib.create_task(
        "Birth?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", tags=[intake_id, "cadence"],
        proposal=_birth_proposal(cand_id))
    receipt_id = task_server.apply_recommendation(tid)
    new_id = task_lib.read_task(receipt_id)["frontmatter"]["program_id"]
    # roadmap-initiative has 2 bootstrap_emissions: draft-ticket + propose-update.
    boot = [t for t in task_lib.list_tasks()
            if new_id in (t.get("tags") or []) and t["id"] != receipt_id]
    assert len(boot) == 2
    # all tagged [new_id, "cadence"]
    for b in boot:
        assert new_id in b["tags"] and "cadence" in b["tags"]
    # a draft-ticket task (agent queue, routes to ticket-creator) and a
    # propose-update recommendation card both exist
    queues = sorted(b.get("queue") for b in boot)
    assert "agent" in queues  # the draft-ticket task
    types = [task_lib.read_task(b["id"])["frontmatter"].get("bootstrap_template")
             for b in boot]
    assert "create-tracker-initiative" in types
    assert "add-roadmap-entry" in types
    # the draft-ticket (agent-queue) task carries task_type=ticket-creator so
    # dispatch scores an exact +100 match deterministically (not a fragile
    # title/description substring match).
    agent_boot = [b for b in boot if b.get("queue") == "agent"]
    assert len(agent_boot) == 1
    assert task_lib.read_task(agent_boot[0]["id"])["frontmatter"].get(
        "task_type") == "ticket-creator"


def test_accept_birth_makes_no_git_commit(tasks_root, tmp_path, monkeypatch):
    """The birth path must not git apply/commit even when PM_OS_DIR IS a repo."""
    import task_server, task_lib
    repo = tmp_path / "repo"; repo.mkdir()
    _git(str(repo), "init"); _git(str(repo), "config", "user.email", "t@e.com")
    _git(str(repo), "config", "user.name", "t")
    (repo / "hello.txt").write_text("hello\n")
    _git(str(repo), "add", "."); _git(str(repo), "commit", "-m", "init")
    head_before = _git(str(repo), "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(repo))
    monkeypatch.setattr(task_server, "_dispatch_bootstrap_task", lambda tid: None)
    intake_id, cand_id = _seed_intake_with_candidate(tmp_path, monkeypatch)
    tid, _ = task_lib.create_task(
        "Birth?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", tags=[intake_id, "cadence"],
        proposal=_birth_proposal(cand_id))
    task_server.apply_recommendation(tid)
    head_after = _git(str(repo), "rev-parse", "HEAD").stdout.strip()
    assert head_after == head_before  # no commit landed


def test_reject_birth_proposal_closes_candidate(tasks_root, tmp_path, monkeypatch):
    import task_server, task_lib, program_lib
    intake_id, cand_id = _seed_intake_with_candidate(tmp_path, monkeypatch)
    tid, _ = task_lib.create_task(
        "Birth?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", tags=[intake_id, "cadence"],
        proposal=_birth_proposal(cand_id))
    task_server.reject_recommendation(tid)
    fm = program_lib.read_program(intake_id, root=str(tmp_path))["frontmatter"]
    cand = next(c for c in fm["items"] if c["id"] == cand_id)
    assert cand["status"] == "closed-with-reason"
    assert cand.get("reason") == "rejected at birth proposal"
    # the card itself is still dismissed (existing reject behavior preserved)
    assert tid not in [t["id"] for t in task_lib.list_tasks()]


def test_accept_archive_proposal_moves_file_no_git(tasks_root, tmp_path, monkeypatch):
    # An archive proposal (op archive) rides the SAME apply_mutation path as
    # advance/adjust (archive is in the closed set) -- no new accept branch. Accept
    # moves the file to programs/archive, sets status archived, completes the card,
    # spawns an informational receipt, and never commits to git.
    import task_server, task_lib, program_lib
    pid = _seed_cadence_program(tmp_path, monkeypatch)
    # NON-repo PM_OS_DIR: a git path would blow up, proving Tier-1.
    monkeypatch.setattr(task_server, "PM_OS_DIR", str(tmp_path / "not-a-repo"))
    tid, _ = task_lib.create_task(
        "archive?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", tags=[pid, "cadence"],
        proposal={"op": "archive", "reason": "reached terminal phase",
                  "citations": ["meeting:X"]})
    receipt_id = task_server.apply_recommendation(tid)

    # archived: file left the active scan, status archived, still resolvable.
    assert pid not in [p["program_id"]
                       for p in program_lib.list_programs(root=str(tmp_path))]
    fm = program_lib.read_program(pid, root=str(tmp_path))["frontmatter"]
    assert fm["status"] == "archived"
    # card completed; informational receipt, NOT a git revert.
    assert tid not in [t["id"] for t in task_lib.list_tasks()]
    rc = task_lib.read_task(receipt_id)["frontmatter"]
    assert rc["card_type"] == "receipt"
    assert not rc.get("revert_commit")


def test_reject_archive_proposal_cancels_card_no_move(tasks_root, tmp_path, monkeypatch):
    # Rejecting an archive proposal just cancels the card -- there is NO candidate
    # to close (that path is birth-only). The program stays active and in place.
    import task_server, task_lib, program_lib
    pid = _seed_cadence_program(tmp_path, monkeypatch)
    tid, _ = task_lib.create_task(
        "archive?", queue="human", card_type="recommendation",
        task_type="cadence-propose-update", tags=[pid, "cadence"],
        proposal={"op": "archive", "reason": "dormant"})
    task_server.reject_recommendation(tid)

    fm = program_lib.read_program(pid, root=str(tmp_path))["frontmatter"]
    assert fm["status"] == "active"  # untouched
    assert pid in [p["program_id"]
                   for p in program_lib.list_programs(root=str(tmp_path))]
    assert tid not in [t["id"] for t in task_lib.list_tasks()]  # card cancelled


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
