import os

import program_lib as pl
import task_lib


def test_create_and_read_roundtrip(tmp_path):
    root = str(tmp_path)
    pid, path = pl.create_program(
        type="roadmap-initiative", title="Payments revamp",
        owner_role="product", root=root,
        frontmatter_extra={"phase": "execution", "drift": "holding"},
        intent="Rebuild reconciliation.")
    assert pid == "PROG-0001"
    assert os.path.isfile(path)
    prog = pl.read_program(pid, root=root)
    assert prog["frontmatter"]["title"] == "Payments revamp"
    assert prog["frontmatter"]["type"] == "roadmap-initiative"
    assert "Rebuild reconciliation." in prog["body"]


def test_next_id_increments(tmp_path):
    root = str(tmp_path)
    a, _ = pl.create_program(type="weekly-priorities", title="A", owner_role="product", root=root)
    b, _ = pl.create_program(type="weekly-priorities", title="B", owner_role="product", root=root)
    assert (a, b) == ("PROG-0001", "PROG-0002")


def test_list_programs_filters_status(tmp_path):
    root = str(tmp_path)
    pl.create_program(type="weekly-priorities", title="active one", owner_role="product",
                      root=root, frontmatter_extra={"status": "active"})
    pl.create_program(type="weekly-priorities", title="archived one", owner_role="product",
                      root=root, frontmatter_extra={"status": "archived"})
    actives = pl.list_programs(status="active", root=root)
    assert [p["frontmatter"]["title"] for p in actives] == ["active one"]


def test_write_roundtrip_validates_yaml(tmp_path):
    root = str(tmp_path)
    pid, path = pl.create_program(type="weekly-priorities", title="X", owner_role="product", root=root)
    prog = pl.read_program(pid, root=root)
    prog["frontmatter"]["title"] = "Edited"
    pl._write_program_file(path, prog["frontmatter"], prog["body"])
    assert pl.read_program(pid, root=root)["frontmatter"]["title"] == "Edited"


# ─── render_view + build_cadence_payload (Task 3) ──────────────────────────────


def test_render_pipeline_current_index(tmp_path):
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="Payments revamp", owner_role="product",
        root=root, intent="Rebuild reconciliation.",
        frontmatter_extra={"phase": "execution", "drift": "holding"})
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["model"] == "pipeline"
    assert vm["current"] == 2  # discovery=0, planning=1, execution=2
    assert vm["family"] == "roadmap"
    assert len(vm["phases"]) == 5
    labels = [ph["label"] for ph in vm["phases"]]
    assert labels == ["Discovery", "Planning", "Execution", "Shipped", "Verified"]
    assert vm["intent"] == "Rebuild reconciliation."


def test_render_pipeline_tolerates_scalar_phase_entered(tmp_path):
    # The Cadence design brief (§4) documents phase_entered as a SCALAR date
    # string (the date the CURRENT phase was entered). render_view must tolerate
    # it without raising — and attribute it to the current phase only.
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="Scalar entered", owner_role="product",
        root=root, frontmatter_extra={
            "phase": "execution", "drift": "holding",
            "phase_entered": "2026-06-01",  # scalar form
        })
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["current"] == 2  # discovery=0, planning=1, execution=2
    # The execution (current) phase carries the scalar date.
    assert vm["phases"][2]["entered"] == "2026-06-01"
    # An earlier/other phase has no entered date.
    assert not vm["phases"][0]["entered"]
    assert not vm["phases"][1]["entered"]


def test_render_target_delta_and_series(tmp_path):
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="did-it-work", title="Smart reconciliation", owner_role="product",
        root=root, frontmatter_extra={
            "drift": "holding",
            "metric": {"actual": 58, "target": 55, "unit": "%"},
            "series": {"pred": [20, 30, 40, 48, 55], "act": [18, 32, 42, 52, 58]},
        })
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["model"] == "target"
    assert vm["metric"] == {"actual": 58, "target": 55, "unit": "%"}
    assert vm["delta_str"] == "+3pt"
    s = vm["series"]
    assert s["predPts"] and isinstance(s["predPts"], str)
    assert s["actPts"] and isinstance(s["actPts"], str)
    assert s["band"] and isinstance(s["band"], str)
    assert s["lastX"] and s["lastY"]
    assert "stroke" not in s  # client owns tone/color

    # Negative delta uses an ASCII hyphen, not the unicode minus the prototype uses.
    pid2, _ = pl.create_program(
        type="did-it-work", title="Inline comments", owner_role="product",
        root=root, frontmatter_extra={
            "drift": "broken",
            "metric": {"actual": 22, "target": 36, "unit": "%"},
            "series": {"pred": [12, 22, 30, 35, 36], "act": [10, 16, 19, 21, 22]},
        })
    vm2 = pl.render_view(pl.read_program(pid2, root=root), reg)
    assert vm2["delta_str"] == "-14pt"
    assert "−" not in vm2["delta_str"]  # no unicode minus


def test_render_cycle(tmp_path):
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="weekly-priorities", title="Weekly priorities", owner_role="product",
        root=root, frontmatter_extra={
            "drift": "holding",
            "status_line": "Sent Monday - 9 of 9 done",
            "periods": [{"w": "W23", "s": "sent"}, {"w": "W24", "s": "late"}],
        })
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["model"] == "cycle"
    assert vm["status_line"] == "Sent Monday - 9 of 9 done"
    assert len(vm["periods"]) == 2
    assert vm["periods"][0] == {"w": "W23", "s": "sent"}
    assert vm["periods"][1] == {"w": "W24", "s": "late"}


def test_render_register(tmp_path):
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="eos-issues", title="Issues list", owner_role="ops",
        root=root, frontmatter_extra={
            "drift": "holding",
            "status_line": "14 open - oldest 16 days",
            "items": [{"name": "Refund timing mismatch", "owner": "ops", "age": 16}],
            "policy": 21,
        })
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["model"] == "register"
    assert len(vm["items"]) == 1
    assert vm["items"][0]["name"] == "Refund timing mismatch"
    assert vm["items"][0]["age"] == 16
    assert vm["policy"] == 21


def test_render_register_unchanged_for_other_register(tmp_path):
    # The program-intake projection must NOT change the plain-register projection
    # for OTHER register programs (eos-issues etc.): they stay {name, owner, age}
    # exactly, no extra candidate fields leak in.
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="eos-issues", title="Issues list", owner_role="ops",
        root=root, frontmatter_extra={
            "drift": "holding",
            "status_line": "14 open - oldest 16 days",
            "items": [{"name": "Refund timing mismatch", "owner": "ops", "age": 16}],
            "policy": 21,
        })
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["model"] == "register"
    assert vm["items"][0] == {"name": "Refund timing mismatch", "owner": "ops", "age": 16}


def test_render_program_intake_surfaces_candidates(tmp_path):
    # The program-intake nursery (a register) carries CANDIDATE items, not the
    # plain {name, owner, age} register shape. render_view must surface each
    # candidate so the Cadence row can list it. Build the candidate the way
    # upsert_candidate actually writes it (NOT a hand-crafted dict) so the test
    # asserts on the REAL shape: name <- title, owner <- program_type,
    # age <- source_count, plus status and possible_duplicate_of when present.
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="program-intake", title="Program intake", owner_role="product",
        root=root, frontmatter_extra={
            "drift": "holding",
            "status_line": "1 candidate",
            "items": [],
            "policy": 30,
        })
    # First evidence opens a candidate; a second source merges (source_count -> 2).
    r1 = pl.upsert_candidate(
        pid, candidate_key="smart-recon", program_type="initiative",
        title="Smart reconciliation", source="meeting:home-standup-0610",
        claim="Team keeps asking for smarter reconciliation.", root=root)
    pl.upsert_candidate(
        pid, candidate_key="smart-recon", program_type="initiative",
        title="Smart reconciliation", source="meeting:payments-sync-0612",
        claim="Same ask surfaced again in payments.", root=root)
    # A second, low-confidence linked candidate -> flagged with possible_duplicate_of.
    r3 = pl.upsert_candidate(
        pid, candidate_key="recon-dup", program_type="initiative",
        title="Reconciliation revamp", source="meeting:platform-0613",
        claim="Possibly the same reconciliation theme.",
        link_to=r1["candidate_id"], confidence=0.4, root=root)
    assert r3["action"] == "flagged"

    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["model"] == "register"
    assert len(vm["items"]) == 2

    by_name = {it["name"]: it for it in vm["items"]}
    # name <- title
    assert "Smart reconciliation" in by_name
    cand = by_name["Smart reconciliation"]
    # owner <- program_type
    assert cand["owner"] == "initiative"
    # age <- source_count (two distinct sources merged)
    assert cand["age"] == 2
    # status is surfaced (open)
    assert cand["status"] == "open"

    dup = by_name["Reconciliation revamp"]
    # possible_duplicate_of is surfaced and points at the first candidate.
    assert dup["possible_duplicate_of"] == r1["candidate_id"]
    assert dup["owner"] == "initiative"


def test_render_view_surfaces_items_for_cycle(tmp_path):
    # A cycle program (weekly-priorities) can declare `items` — the week's
    # priorities. render_view must surface them in the view model so the Cadence
    # row can list them. The canonical cycle-item shape is the SEED's shape:
    # {id, label, owner_role, status} (role-referenced, invariant #1 compliant).
    # render_view maps label -> name and owner_role -> owner, and includes status.
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="weekly-priorities", title="Weekly priorities", owner_role="product",
        root=root, frontmatter_extra={
            "drift": "holding",
            "status_line": "Sent Monday - 9 of 9 done",
            "items": [
                {"id": "close-payments-prd", "label": "Close payments PRD",
                 "owner_role": "product", "status": "open"},
                {"id": "review-home-backlog", "label": "Review home backlog",
                 "owner_role": "engineering", "status": "open"},
            ],
        })
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["model"] == "cycle"
    assert len(vm["items"]) == 2
    # The label -> name mapping works (non-null).
    assert vm["items"][0]["name"] == "Close payments PRD"
    assert vm["items"][0]["name"] is not None
    # The owner_role -> owner mapping works (a role token, non-null).
    assert vm["items"][1]["owner"] == "engineering"
    assert vm["items"][1]["owner"] is not None
    # status is included.
    assert vm["items"][0]["status"] == "open"
    assert vm["items"][1]["status"] == "open"


def test_render_view_includes_digests_when_artifacts_exist(tmp_path):
    # When a cycle program has written versioned digest artifacts, render_view
    # (given the root) projects a newest-first `digests` list capped at 3, each
    # {slug, version, path}.
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="weekly-priorities", title="Weekly priorities", owner_role="product",
        root=root, frontmatter_extra={"drift": "holding"})
    pl.write_artifact(pid, "2026-W24-priorities", "w24 body", root=root)
    pl.write_artifact(pid, "2026-W25-priorities", "w25 body", root=root)
    vm = pl.render_view(pl.read_program(pid, root=root), reg, root=root)
    assert vm["digests"]  # non-empty
    # Newest-first: W25 leads W24 (sort by slug desc).
    assert vm["digests"][0]["slug"] == "2026-W25-priorities"
    assert vm["digests"][0]["version"] == 1
    assert vm["digests"][0]["path"].endswith("2026-W25-priorities-v1.md")
    assert vm["digests"][1]["slug"] == "2026-W24-priorities"


def test_render_view_digests_default_empty_without_root(tmp_path):
    # render_view without a root (existing call-site shape) tolerates artifacts
    # being unreachable -> digests is [].
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="weekly-priorities", title="Weekly priorities", owner_role="product",
        root=root, frontmatter_extra={"drift": "holding"})
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["digests"] == []


def test_render_activity_from_observations():
    reg = pl.load_registry()
    program = {
        "frontmatter": {
            "program_id": "PROG-9001",
            "type": "roadmap-initiative",
            "title": "Hand-built",
            "phase": "discovery",
            "drift": "holding",
        },
        "body": (
            "## Intent\nA stated intent.\n\n"
            "## Observations\n"
            "### 2026-06-11 - sentinel:movement-watch [status-signal]\n"
            "claim: Closed 4 of 9 stories.\n"
            "source: tracker\n\n"
            "## Cycles\n"
        ),
    }
    vm = pl.render_view(program, reg)
    assert vm["intent"] == "A stated intent."
    assert len(vm["activity"]) == 1
    entry = vm["activity"][0]
    assert entry["date"] == "2026-06-11"
    assert entry["text"] == "Closed 4 of 9 stories."
    assert entry["tag"] == "movement-watch"


def test_render_activity_absent_degrades_to_empty():
    reg = pl.load_registry()
    program = {
        "frontmatter": {"type": "weekly-priorities", "title": "No obs", "drift": "holding"},
        "body": "## Intent\nJust intent.\n",
    }
    vm = pl.render_view(program, reg)
    assert vm["activity"] == []


def test_build_series_tolerates_short_act():
    # act shorter than pred must not raise (one bad series would 500 the endpoint).
    s = pl._build_series({"pred": [10, 20, 30], "act": [10, 20]})
    assert isinstance(s, dict)
    for k in ("predPts", "actPts", "band", "lastX", "lastY"):
        assert k in s


def test_render_activity_parses_emdash_header():
    reg = pl.load_registry()
    program = {
        "frontmatter": {
            "program_id": "PROG-9002",
            "type": "roadmap-initiative",
            "title": "Em-dash header",
            "phase": "discovery",
            "drift": "holding",
        },
        "body": (
            "## Intent\nIntent text.\n\n"
            "## Observations\n"
            "### 2026-06-11 — sentinel:movement-watch [status-signal]\n"
            "claim: Closed 4 of 9 stories.\n\n"
            "## Cycles\n"
        ),
    }
    vm = pl.render_view(program, reg)
    assert len(vm["activity"]) == 1
    entry = vm["activity"][0]
    assert entry["date"] == "2026-06-11"
    assert entry["text"] == "Closed 4 of 9 stories."
    assert entry["tag"] == "movement-watch"


def test_render_checkpoints_use_canonical_keys(tmp_path):
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="Checkpointed", owner_role="product",
        root=root, frontmatter_extra={
            "phase": "execution", "drift": "holding",
            "checkpoints": [
                {"id": "cp1", "label": "Beta cut", "due": "2026-07-01",
                 "instrument": "tracker", "status": "open"},
            ],
        })
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert len(vm["checkpoints"]) == 1
    cp = vm["checkpoints"][0]
    assert cp["label"] == "Beta cut"
    assert cp["due"] == "2026-07-01"
    assert cp["instrument"] == "tracker"
    assert cp["status"] == "open"


def test_build_payload_groups_by_family_and_drops_empty(tmp_path):
    root = str(tmp_path)
    pl.create_program(
        type="roadmap-initiative", title="Roadmap one", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})
    pl.create_program(
        type="did-it-work", title="Outcome one", owner_role="product",
        root=root, frontmatter_extra={
            "drift": "holding",
            "metric": {"actual": 50, "target": 50, "unit": "%"},
            "series": {"pred": [10, 20, 30], "act": [10, 20, 30]},
        })
    payload = pl.build_cadence_payload(root=root)
    fam_ids = [f["id"] for f in payload["families"]]
    assert fam_ids == ["roadmap", "outcomes"]  # registry order, empties dropped
    assert "weekly" not in fam_ids
    assert "eos" not in fam_ids
    roadmap = payload["families"][0]
    assert roadmap["label"] == "Roadmap"
    assert len(roadmap["programs"]) == 1
    assert roadmap["programs"][0]["name"] == "Roadmap one"


def test_cadence_payload_is_json_clean():
    # ruamel parses UNQUOTED ISO dates in program frontmatter (checkpoint `due`,
    # `phase_entered`, binding `last`) into datetime.date objects. The payload
    # must be strictly JSON-clean so a future caller can json.dumps() it with
    # the DEFAULT encoder (no default=str crutch) without raising.
    import json
    payload = pl.build_cadence_payload()  # real datasets root (seeds carry date objects)
    # Must succeed WITHOUT a default= argument. RED before the fix (TypeError).
    json.dumps(payload)

    # Values are preserved as ISO strings, not mangled. PROG-0001's seed has an
    # unquoted checkpoint due of 2026-05-19; it must surface as that exact string.
    reg = pl.load_registry()
    prog = pl.read_program("PROG-0001")
    vm = pl.render_view(prog, reg)
    # render_view itself must be json.dumps-clean on its own.
    json.dumps(vm)
    dues = [cp["due"] for cp in vm["checkpoints"]]
    assert "2026-05-19" in dues
    for d in dues:
        assert isinstance(d, str)


# ─── needs_you count (Task 6) ──────────────────────────────────────────────────


def test_render_view_needs_you_default_zero(tmp_path):
    # Omitting the arg keeps needs_you at 0 (default keeps existing call sites valid).
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="No needs", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["needs_you"] == 0


def test_render_view_needs_you_explicit(tmp_path):
    # When passed explicitly, needs_you carries through into the JSON-clean vm.
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="Three needs", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})
    vm = pl.render_view(pl.read_program(pid, root=root), reg, needs_you=3)
    assert vm["needs_you"] == 3
    import json
    json.dumps(vm)  # needs_you is inside the _jsonable() return — stays JSON-clean.


def _seed_isolated_queues(tmp_path, monkeypatch):
    """Repoint task_lib at a fresh tmp tasks dir (mirrors test_cadence_reconcile)."""
    tasks_dir = tmp_path / "tasks"
    for q in ("human", "agent", "collab", "waiting"):
        (tasks_dir / q).mkdir(parents=True, exist_ok=True)
    counter = tasks_dir / "_counter"
    counter.write_text("1")
    archive = tasks_dir / "_archive"
    archive.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(task_lib, "TASKS_DIR", str(tasks_dir))
    monkeypatch.setattr(task_lib, "COUNTER_FILE", str(counter))
    # ARCHIVE_DIR is a module constant computed at import from the ORIGINAL
    # TASKS_DIR, so it must be patched too -- else complete_task writes (and
    # list_archived reads) the real datasets/tasks/_archive, breaking isolation.
    monkeypatch.setattr(task_lib, "ARCHIVE_DIR", str(archive))
    return tasks_dir


def test_build_payload_needs_you_counts_tagged_open_human_card(tmp_path, monkeypatch):
    _seed_isolated_queues(tmp_path, monkeypatch)
    root = str(tmp_path)
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="Flagged program", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "broken"})
    # One OPEN human card tagged with that program id (how the emitter tags it).
    task_lib.create_task(
        title="Cadence escalation", queue="human", priority="high",
        tags=[pid, "cadence"], description="Flagged.")

    payload = pl.build_cadence_payload(root=root)
    prog = payload["families"][0]["programs"][0]
    assert prog["id"] == pid
    assert prog["needs_you"] == 1


def test_build_payload_needs_you_zero_when_untagged(tmp_path, monkeypatch):
    _seed_isolated_queues(tmp_path, monkeypatch)
    root = str(tmp_path)
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="Unflagged program", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})
    # An open human card tagged with a DIFFERENT program id must not be counted.
    task_lib.create_task(
        title="Other escalation", queue="human", priority="high",
        tags=["PROG-9999", "cadence"], description="Other.")

    payload = pl.build_cadence_payload(root=root)
    prog = payload["families"][0]["programs"][0]
    assert prog["id"] == pid
    assert prog["needs_you"] == 0


def test_build_payload_needs_you_resilient_when_task_lib_raises(tmp_path, monkeypatch):
    # A task-system failure must NEVER break the payload — all needs_you fall to 0.
    root = str(tmp_path)
    pl.create_program(
        type="roadmap-initiative", title="Resilient program", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})

    def _boom(*a, **k):
        raise RuntimeError("task system down")

    monkeypatch.setattr(task_lib, "list_tasks", _boom)
    payload = pl.build_cadence_payload(root=root)  # must not raise
    prog = payload["families"][0]["programs"][0]
    assert prog["needs_you"] == 0


# ─── observation ledger (Task 8) ────────────────────────────────────────────────


def test_render_view_projects_observations_with_source():
    # The richer `observations` list exposes the source line that `activity`
    # drops, plus the sentinel and kind off the header.
    reg = pl.load_registry()
    program = {
        "frontmatter": {
            "program_id": "PROG-9010",
            "type": "roadmap-initiative",
            "title": "Ledger",
            "phase": "discovery",
            "drift": "holding",
        },
        "body": (
            "## Intent\nA stated intent.\n\n"
            "## Observations\n"
            "### 2026-06-11 - sentinel:movement-watch [status-signal]\n"
            "source: datasets/meetings/2026-06-11_x.md (#Action Items)\n"
            "claim: Closed 4 of 9 stories.\n\n"
            "## Cycles\n"
        ),
    }
    vm = pl.render_view(program, reg)
    assert len(vm["observations"]) == 1
    obs = vm["observations"][0]
    assert obs["date"] == "2026-06-11"
    assert obs["kind"] == "status-signal"
    assert obs["sentinel"] == "movement-watch"
    assert obs["source"] == "datasets/meetings/2026-06-11_x.md (#Action Items)"
    assert obs["claim"] == "Closed 4 of 9 stories."
    # The existing activity field is preserved (other code reads it).
    assert len(vm["activity"]) == 1


def test_render_view_observations_empty_when_absent():
    reg = pl.load_registry()
    program = {
        "frontmatter": {"type": "weekly-priorities", "title": "No obs", "drift": "holding"},
        "body": "## Intent\nJust intent.\n",
    }
    vm = pl.render_view(program, reg)
    assert vm["observations"] == []


def test_render_view_emissions_pass_through():
    # render_view accepts an `emissions` arg and passes it into the view model.
    reg = pl.load_registry()
    program = {
        "frontmatter": {
            "program_id": "PROG-9011", "type": "roadmap-initiative",
            "title": "Emits", "phase": "discovery", "drift": "holding",
        },
        "body": "## Intent\nIntent.\n",
    }
    ems = [{"id": "TASK-0001", "kind": "escalate", "title": "Needs attention",
            "status": "pending", "created": "2026-06-11T09:00:00"}]
    vm = pl.render_view(program, reg, emissions=ems)
    assert vm["emissions"] == ems
    import json
    json.dumps(vm)  # stays JSON-clean


def test_render_view_emissions_default_empty(tmp_path):
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="No emits", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["emissions"] == []


def test_build_payload_joins_emissions_per_program(tmp_path, monkeypatch):
    _seed_isolated_queues(tmp_path, monkeypatch)
    root = str(tmp_path)
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="Emitting program", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "broken"})

    # An open escalate card: creator=cadence, tagged [pid, "cadence"].
    task_lib.create_task(
        title="Emitting program needs attention", queue="human", priority="high",
        creator="cadence", tags=[pid, "cadence"], description="Flagged.")
    # An open propose-update card carrying the program tag + a proposal.
    task_lib.create_task(
        title="Emitting program: advance to verified?", queue="human", priority="high",
        creator="cadence", card_type="recommendation", task_type="cadence-propose-update",
        tags=[pid, "cadence"], proposal={"op": "advance-phase", "to": "verified"},
        description="Proposed advance.")
    # A completed receipt carrying the program_id field (how the accept handler tags it).
    rid, _ = task_lib.create_task(
        title="Applied: advance", queue="human", creator="agent", card_type="receipt",
        description="Applied a program update.")
    task_lib.update_task(rid, changes={
        "receipt_kind": "cadence-apply", "source_recommendation": "TASK-0002",
        "program_id": pid})
    task_lib.complete_task(rid, actor="human")

    payload = pl.build_cadence_payload(root=root)
    prog = payload["families"][0]["programs"][0]
    assert prog["id"] == pid
    kinds = sorted(e["kind"] for e in prog["emissions"])
    assert kinds == ["escalate", "propose-update", "receipt"]
    by_kind = {e["kind"]: e for e in prog["emissions"]}
    assert by_kind["escalate"]["status"] == "pending"
    assert by_kind["propose-update"]["status"] == "pending"
    assert by_kind["receipt"]["status"] == "sent"  # done receipt -> sent
    import json
    json.dumps(payload)  # the joined payload stays JSON-clean


def test_build_payload_emissions_resilient_when_task_lib_raises(tmp_path, monkeypatch):
    # A task-system failure must NEVER break the payload: emissions -> [], needs_you -> 0.
    root = str(tmp_path)
    pl.create_program(
        type="roadmap-initiative", title="Resilient emits", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})

    def _boom(*a, **k):
        raise RuntimeError("task system down")

    monkeypatch.setattr(task_lib, "list_tasks", _boom)
    payload = pl.build_cadence_payload(root=root)  # must not raise
    prog = payload["families"][0]["programs"][0]
    assert prog["emissions"] == []
    assert prog["needs_you"] == 0


def test_all_seed_programs_render():
    reg = pl.load_registry()
    progs = pl.list_programs()  # real datasets root
    # 13 original seeds + PROG-0014 (the program-intake nursery, inc4a).
    assert len(progs) == 14     # concrete count: a silently-dropped/malformed seed fails here
    for p in progs:
        vm = pl.render_view(p, reg)
        assert vm["model"] in {"pipeline", "target", "cycle", "register"}
        assert vm["name"]
    # the family payload groups them and drops no expected family. The nursery
    # adds the System family, which shelves last (order 99).
    payload = pl.build_cadence_payload()
    fam_ids = [f["id"] for f in payload["families"]]
    assert fam_ids == ["roadmap", "weekly", "outcomes", "eos", "system"]


# ─── tracker_anchor (Task 5, shared binding resolution) ──────────────────────

def test_tracker_anchor_reads_bindings_anchor():
    fm = {"bindings": [
        {"id": "tracker", "role": "truth", "kind": "project_management",
         "anchor": "EPIC-204", "mode": "read"}]}
    assert pl.tracker_anchor(fm) == "EPIC-204"


def test_tracker_anchor_falls_back_to_links_tracker_epic():
    fm = {"bindings": [{"role": "mirror", "kind": "transcripts", "anchor": "X"}],
          "links": {"tracker_epic": "EPIC-999"}}
    assert pl.tracker_anchor(fm) == "EPIC-999"


def test_tracker_anchor_prefers_binding_over_links():
    fm = {"bindings": [
              {"role": "truth", "kind": "project_management", "anchor": "EPIC-1"}],
          "links": {"tracker_epic": "EPIC-2"}}
    assert pl.tracker_anchor(fm) == "EPIC-1"


def test_tracker_anchor_none_when_neither():
    assert pl.tracker_anchor({}) is None
    assert pl.tracker_anchor({"bindings": [], "links": {}}) is None


def test_tracker_anchor_robust_to_malformed():
    # bindings not a list, entries not dicts, links not a dict -> None, no raise.
    assert pl.tracker_anchor({"bindings": "nope"}) is None
    assert pl.tracker_anchor({"bindings": ["str", 7, None]}) is None
    assert pl.tracker_anchor({"bindings": [{"role": "truth",
                                            "kind": "project_management"}]}) is None
    assert pl.tracker_anchor({"links": "nope"}) is None
    assert pl.tracker_anchor(None) is None


# ─── upsert_candidate + close/birth + _norm_title_key (Task 3, the nursery) ────

def _seed_intake(tmp_path, **extra):
    """Create a program-intake register program with an empty `items` list.

    Mirrors the seeded PROG-0014 shape. Returns its program_id.
    """
    fm_extra = {"drift": "holding", "status_line": "0 candidates",
                "items": [], "policy": 30}
    fm_extra.update(extra)
    pid, _ = pl.create_program(
        type="program-intake", title="Program intake", owner_role="product",
        root=str(tmp_path), frontmatter_extra=fm_extra)
    return pid


def test_norm_title_key_normalizes():
    # lowercase, strip punctuation, collapse whitespace.
    assert pl._norm_title_key("Smart Reconciliation!") == "smart reconciliation"
    assert pl._norm_title_key("  Smart   reconciliation  ") == "smart reconciliation"
    assert pl._norm_title_key("Re-org: the Plan (v2)") == "reorg the plan v2"
    # Two titles that differ only by punctuation/case/space collapse to the same key.
    assert pl._norm_title_key("Foo, Bar.") == pl._norm_title_key("foo bar")
    # Empty / whitespace-only -> empty string (never raises).
    assert pl._norm_title_key("") == ""
    assert pl._norm_title_key("   ") == ""


def test_upsert_candidate_opens_new(tmp_path):
    ip = _seed_intake(tmp_path)
    res = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-A",
        claim="Mentioned in planning.", root=str(tmp_path))
    assert res["action"] == "opened"
    assert res["candidate_id"] == "CAND-0001"
    assert res["source_count"] == 1
    prog = pl.read_program(ip, root=str(tmp_path))
    items = prog["frontmatter"]["items"]
    assert len(items) == 1
    cand = items[0]
    assert cand["id"] == "CAND-0001"
    assert cand["program_type"] == "roadmap-initiative"
    assert cand["title"] == "Smart reconciliation"
    assert cand["status"] == "open"
    assert cand["source_count"] == 1
    assert len(cand["evidence"]) == 1
    ev = cand["evidence"][0]
    assert ev["source"] == "meeting-A"
    assert ev["claim"] == "Mentioned in planning."
    assert ev["sentinel"] == "program-intake"  # default
    assert ev["date"]  # defaulted to today


def test_upsert_candidate_default_declared_is_false(tmp_path):
    ip = _seed_intake(tmp_path)
    pl.upsert_candidate(
        ip, candidate_key="k1", program_type="eos-rock",
        title="Q3 rock", source="meeting-A", claim="Mentioned.",
        root=str(tmp_path))
    items = pl.read_program(ip, root=str(tmp_path))["frontmatter"]["items"]
    assert items[0].get("declared", False) is False


def test_upsert_candidate_declared_true_opens_declared(tmp_path):
    ip = _seed_intake(tmp_path)
    pl.upsert_candidate(
        ip, candidate_key="k1", program_type="eos-rock",
        title="Q3 rock", source="meeting-A", claim="We are committing to this.",
        declared=True, root=str(tmp_path))
    items = pl.read_program(ip, root=str(tmp_path))["frontmatter"]["items"]
    assert items[0]["declared"] is True


def test_upsert_candidate_declared_is_sticky_true_on_merge(tmp_path):
    ip = _seed_intake(tmp_path)
    # First mention declares it.
    pl.upsert_candidate(
        ip, candidate_key="k1", program_type="eos-rock",
        title="Q3 rock", source="meeting-A", claim="We are committing to this.",
        anchor="ROCK-1", declared=True, root=str(tmp_path))
    # A later, non-declaring mention merges in (same anchor) but must NOT undeclare.
    pl.upsert_candidate(
        ip, candidate_key="k2", program_type="eos-rock",
        title="Q3 rock again", source="meeting-B", claim="Discussed again.",
        anchor="ROCK-1", declared=False, root=str(tmp_path))
    items = pl.read_program(ip, root=str(tmp_path))["frontmatter"]["items"]
    assert len(items) == 1
    assert items[0]["declared"] is True


def test_upsert_candidate_declared_set_true_on_later_merge(tmp_path):
    ip = _seed_intake(tmp_path)
    # First mention does not declare.
    pl.upsert_candidate(
        ip, candidate_key="k1", program_type="eos-rock",
        title="Q3 rock", source="meeting-A", claim="Mentioned.",
        anchor="ROCK-2", root=str(tmp_path))
    # A later declaring mention merges in and flips declared sticky-true.
    pl.upsert_candidate(
        ip, candidate_key="k2", program_type="eos-rock",
        title="Q3 rock", source="meeting-B", claim="Now we commit.",
        anchor="ROCK-2", declared=True, root=str(tmp_path))
    items = pl.read_program(ip, root=str(tmp_path))["frontmatter"]["items"]
    assert len(items) == 1
    assert items[0]["declared"] is True


def test_upsert_candidate_mints_sequential_ids(tmp_path):
    ip = _seed_intake(tmp_path)
    a = pl.upsert_candidate(ip, candidate_key="k1", program_type="roadmap-initiative",
                            title="Alpha", source="s1", claim="c1", root=str(tmp_path))
    b = pl.upsert_candidate(ip, candidate_key="k2", program_type="roadmap-initiative",
                            title="Beta", source="s2", claim="c2", root=str(tmp_path))
    assert (a["candidate_id"], b["candidate_id"]) == ("CAND-0001", "CAND-0002")


def test_upsert_candidate_merges_by_anchor(tmp_path):
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-A", claim="Mentioned.",
        anchor="EPIC-42", root=str(tmp_path))
    # Different title, same anchor -> merges into the first candidate.
    second = pl.upsert_candidate(
        ip, candidate_key="k2", program_type="roadmap-initiative",
        title="Totally different name", source="meeting-B", claim="Raised again.",
        anchor="EPIC-42", root=str(tmp_path))
    assert second["action"] == "merged"
    assert second["candidate_id"] == first["candidate_id"]
    assert second["source_count"] == 2
    prog = pl.read_program(ip, root=str(tmp_path))
    assert len(prog["frontmatter"]["items"]) == 1
    assert len(prog["frontmatter"]["items"][0]["evidence"]) == 2


def test_upsert_candidate_merges_by_title_key(tmp_path):
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart Reconciliation", source="meeting-A", claim="c1",
        root=str(tmp_path))
    # Same normalized title (case/punct differ), no anchor -> merges.
    second = pl.upsert_candidate(
        ip, candidate_key="k2", program_type="roadmap-initiative",
        title="smart reconciliation!", source="meeting-B", claim="c2",
        root=str(tmp_path))
    assert second["action"] == "merged"
    assert second["candidate_id"] == first["candidate_id"]
    assert second["source_count"] == 2


def test_upsert_candidate_title_merge_is_type_gated(tmp_path):
    # Two candidates with the SAME normalized title but DIFFERENT program_type
    # must NOT merge on the title-key path: birthing the wrong-typed program is
    # the bug being fixed. The second upsert opens a brand-new candidate.
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart Reconciliation", source="meeting-A", claim="c1",
        root=str(tmp_path))
    second = pl.upsert_candidate(
        ip, candidate_key="k2", program_type="eos-rock",
        title="smart reconciliation!", source="meeting-B", claim="c2",
        root=str(tmp_path))
    assert second["action"] == "opened"
    assert second["candidate_id"] != first["candidate_id"]
    prog = pl.read_program(ip, root=str(tmp_path))
    items = {it["id"]: it for it in prog["frontmatter"]["items"]}
    assert len(items) == 2
    assert items[first["candidate_id"]]["program_type"] == "roadmap-initiative"
    assert items[second["candidate_id"]]["program_type"] == "eos-rock"


def test_upsert_candidate_title_merge_same_type_still_merges(tmp_path):
    # Same normalized title AND same program_type -> still merges (the existing
    # title-key behavior is preserved by the type gate).
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart Reconciliation", source="meeting-A", claim="c1",
        root=str(tmp_path))
    second = pl.upsert_candidate(
        ip, candidate_key="k2", program_type="roadmap-initiative",
        title="smart reconciliation!", source="meeting-B", claim="c2",
        root=str(tmp_path))
    assert second["action"] == "merged"
    assert second["candidate_id"] == first["candidate_id"]
    assert second["source_count"] == 2


def test_upsert_candidate_merges_by_confident_link(tmp_path):
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Alpha initiative", source="meeting-A", claim="c1", root=str(tmp_path))
    # link_to resolves to the open candidate, confidence >= 0.8 -> merge.
    second = pl.upsert_candidate(
        ip, candidate_key="k2", program_type="roadmap-initiative",
        title="A loosely-related name", source="meeting-B", claim="c2",
        link_to=first["candidate_id"], confidence=0.85, root=str(tmp_path))
    assert second["action"] == "merged"
    assert second["candidate_id"] == first["candidate_id"]
    assert second["source_count"] == 2


def test_upsert_candidate_flags_unsure_link(tmp_path):
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Alpha initiative", source="meeting-A", claim="c1", root=str(tmp_path))
    # link_to resolves but confidence < 0.8 -> new candidate carrying the marker.
    second = pl.upsert_candidate(
        ip, candidate_key="k2", program_type="roadmap-initiative",
        title="Maybe-related name", source="meeting-B", claim="c2",
        link_to=first["candidate_id"], confidence=0.5, root=str(tmp_path))
    assert second["action"] == "flagged"
    assert second["candidate_id"] != first["candidate_id"]
    prog = pl.read_program(ip, root=str(tmp_path))
    items = {it["id"]: it for it in prog["frontmatter"]["items"]}
    assert items[second["candidate_id"]]["possible_duplicate_of"] == first["candidate_id"]


def test_upsert_candidate_flags_link_without_confidence(tmp_path):
    # link_to resolves but no confidence at all -> flagged (below threshold).
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Alpha initiative", source="meeting-A", claim="c1", root=str(tmp_path))
    second = pl.upsert_candidate(
        ip, candidate_key="k2", program_type="roadmap-initiative",
        title="No-confidence name", source="meeting-B", claim="c2",
        link_to=first["candidate_id"], root=str(tmp_path))
    assert second["action"] == "flagged"
    prog = pl.read_program(ip, root=str(tmp_path))
    items = {it["id"]: it for it in prog["frontmatter"]["items"]}
    assert items[second["candidate_id"]]["possible_duplicate_of"] == first["candidate_id"]


def test_upsert_candidate_non_numeric_confidence_flags(tmp_path):
    # A sentinel emits a non-numeric confidence (e.g. "high") on a resolvable
    # link -> defensively treated as below-threshold (flagged), never raises.
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Alpha initiative", source="meeting-A", claim="c1", root=str(tmp_path))
    second = pl.upsert_candidate(
        ip, candidate_key="k2", program_type="roadmap-initiative",
        title="Vague name", source="meeting-B", claim="c2",
        link_to=first["candidate_id"], confidence="high", root=str(tmp_path))
    assert second["action"] == "flagged"
    assert second["candidate_id"] != first["candidate_id"]
    prog = pl.read_program(ip, root=str(tmp_path))
    items = {it["id"]: it for it in prog["frontmatter"]["items"]}
    assert items[second["candidate_id"]]["possible_duplicate_of"] == first["candidate_id"]


def test_upsert_candidate_anchor_precedence_over_link(tmp_path):
    # When BOTH a matching anchor AND a link_to (to a different open candidate)
    # are supplied, the anchor match wins: merged into the anchor candidate, the
    # link is ignored, and possible_duplicate_of is NOT set.
    ip = _seed_intake(tmp_path)
    anchored = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Anchored initiative", source="meeting-A", claim="c1",
        anchor="EPIC-77", root=str(tmp_path))
    other = pl.upsert_candidate(
        ip, candidate_key="k2", program_type="roadmap-initiative",
        title="Other open candidate", source="meeting-B", claim="c2",
        root=str(tmp_path))
    # Same anchor AND a link to the OTHER candidate -> anchor wins.
    res = pl.upsert_candidate(
        ip, candidate_key="k3", program_type="roadmap-initiative",
        title="Different name entirely", source="meeting-C", claim="c3",
        anchor="EPIC-77", link_to=other["candidate_id"], confidence=0.99,
        root=str(tmp_path))
    assert res["action"] == "merged"
    assert res["candidate_id"] == anchored["candidate_id"]
    assert res["candidate_id"] != other["candidate_id"]
    prog = pl.read_program(ip, root=str(tmp_path))
    items = {it["id"]: it for it in prog["frontmatter"]["items"]}
    # Merged into the anchored candidate; the link was ignored.
    assert "possible_duplicate_of" not in items[anchored["candidate_id"]]
    assert len(items[anchored["candidate_id"]]["evidence"]) == 2
    # The other candidate is untouched.
    assert len(items[other["candidate_id"]]["evidence"]) == 1


def test_upsert_candidate_distinct_source_counting(tmp_path):
    # Same source twice -> source_count stays 1 (it counts DISTINCT sources).
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-A", claim="c1",
        anchor="EPIC-7", root=str(tmp_path))
    assert first["source_count"] == 1
    again = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-A", claim="c2 different claim",
        anchor="EPIC-7", root=str(tmp_path))
    assert again["action"] == "merged"
    assert again["source_count"] == 1  # same source -> still one distinct source
    prog = pl.read_program(ip, root=str(tmp_path))
    cand = prog["frontmatter"]["items"][0]
    assert len(cand["evidence"]) == 2  # but the evidence list still appends (append-only)
    assert cand["source_count"] == 1
    # A third, distinct source -> 2.
    third = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-B", claim="c3",
        anchor="EPIC-7", root=str(tmp_path))
    assert third["source_count"] == 2


def test_upsert_candidate_closed_match_creates_new(tmp_path):
    # A key/anchor that matches only a CLOSED candidate creates a brand-new one.
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-A", claim="c1",
        anchor="EPIC-9", root=str(tmp_path))
    pl.close_candidate(ip, first["candidate_id"], reason="declined",
                       root=str(tmp_path))
    # Same anchor + title, but the matching candidate is closed -> a new candidate.
    second = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-B", claim="c2",
        anchor="EPIC-9", root=str(tmp_path))
    assert second["action"] == "opened"
    assert second["candidate_id"] != first["candidate_id"]
    prog = pl.read_program(ip, root=str(tmp_path))
    assert len(prog["frontmatter"]["items"]) == 2


def test_upsert_candidate_does_not_append_to_birthed(tmp_path):
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-A", claim="c1",
        anchor="EPIC-9", root=str(tmp_path))
    pl.mark_candidate_birthed(ip, first["candidate_id"], "PROG-0099",
                              root=str(tmp_path))
    second = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-B", claim="c2",
        anchor="EPIC-9", root=str(tmp_path))
    assert second["action"] == "opened"
    assert second["candidate_id"] != first["candidate_id"]


def test_upsert_candidate_evidence_is_append_only(tmp_path):
    ip = _seed_intake(tmp_path)
    pl.upsert_candidate(ip, candidate_key="k1", program_type="roadmap-initiative",
                        title="Alpha", source="s1", claim="first", anchor="E1",
                        root=str(tmp_path))
    pl.upsert_candidate(ip, candidate_key="k1", program_type="roadmap-initiative",
                        title="Alpha", source="s2", claim="second", anchor="E1",
                        root=str(tmp_path))
    prog = pl.read_program(ip, root=str(tmp_path))
    claims = [e["claim"] for e in prog["frontmatter"]["items"][0]["evidence"]]
    assert claims == ["first", "second"]  # prior evidence never rewritten


def test_upsert_candidate_accepts_sentinel_kwarg(tmp_path):
    ip = _seed_intake(tmp_path)
    res = pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Alpha", source="s1", claim="c1", sentinel="program-intake-v2",
        root=str(tmp_path))
    prog = pl.read_program(ip, root=str(tmp_path))
    assert prog["frontmatter"]["items"][0]["evidence"][0]["sentinel"] == "program-intake-v2"
    assert res["action"] == "opened"


def test_close_candidate_sets_status_and_reason(tmp_path):
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(ip, candidate_key="k1", program_type="roadmap-initiative",
                                title="Alpha", source="s1", claim="c1", root=str(tmp_path))
    pl.close_candidate(ip, first["candidate_id"], reason="not worth it",
                       root=str(tmp_path))
    prog = pl.read_program(ip, root=str(tmp_path))
    cand = prog["frontmatter"]["items"][0]
    assert cand["status"] == "closed-with-reason"
    assert cand["reason"] == "not worth it"


def test_close_candidate_idempotent(tmp_path):
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(ip, candidate_key="k1", program_type="roadmap-initiative",
                                title="Alpha", source="s1", claim="c1", root=str(tmp_path))
    pl.close_candidate(ip, first["candidate_id"], reason="r1", root=str(tmp_path))
    # Closing again is a no-op success (reason preserved, no raise).
    pl.close_candidate(ip, first["candidate_id"], reason="r2", root=str(tmp_path))
    prog = pl.read_program(ip, root=str(tmp_path))
    cand = prog["frontmatter"]["items"][0]
    assert cand["status"] == "closed-with-reason"
    assert cand["reason"] == "r1"  # first reason retained (idempotent)


def test_mark_candidate_birthed_sets_status_and_link(tmp_path):
    ip = _seed_intake(tmp_path)
    first = pl.upsert_candidate(ip, candidate_key="k1", program_type="roadmap-initiative",
                                title="Alpha", source="s1", claim="c1", root=str(tmp_path))
    pl.mark_candidate_birthed(ip, first["candidate_id"], "PROG-0050",
                              root=str(tmp_path))
    prog = pl.read_program(ip, root=str(tmp_path))
    cand = prog["frontmatter"]["items"][0]
    assert cand["status"] == "birthed"
    assert cand["born_program_id"] == "PROG-0050"


def test_close_and_mark_unknown_candidate_raises(tmp_path):
    ip = _seed_intake(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        pl.close_candidate(ip, "CAND-9999", reason="x", root=str(tmp_path))
    with pytest.raises(ValueError):
        pl.mark_candidate_birthed(ip, "CAND-9999", "PROG-1", root=str(tmp_path))


def test_upsert_candidate_empty_title_raises(tmp_path):
    ip = _seed_intake(tmp_path)
    import pytest
    with pytest.raises(ValueError):
        pl.upsert_candidate(ip, candidate_key="k1", program_type="roadmap-initiative",
                            title="", source="s1", claim="c1", root=str(tmp_path))


def test_upsert_candidate_no_anchor_no_link_opens_when_no_title_match(tmp_path):
    # Missing both anchor AND link_to, and no title-key match -> a fresh candidate.
    ip = _seed_intake(tmp_path)
    a = pl.upsert_candidate(ip, candidate_key="k1", program_type="roadmap-initiative",
                            title="Alpha", source="s1", claim="c1", root=str(tmp_path))
    b = pl.upsert_candidate(ip, candidate_key="k2", program_type="roadmap-initiative",
                            title="Beta", source="s2", claim="c2", root=str(tmp_path))
    assert a["action"] == "opened" and b["action"] == "opened"
    assert a["candidate_id"] != b["candidate_id"]


# ─── birth_program (Task 4, the birth path: pure file-creation) ───────────────

def test_birth_program_pipeline_creates_active_at_first_phase(tmp_path):
    root = str(tmp_path)
    new_id = pl.birth_program(
        {
            "program_type": "roadmap-initiative",
            "title": "Smart reconciliation",
            "checkpoints": [
                {"id": "discovery-exit", "label": "Discovery exit", "due": "2026-07-01"},
            ],
            "citations": ["meeting-A", "meeting-B"],
        },
        root=root,
    )
    prog = pl.read_program(new_id, root=root)
    fm = prog["frontmatter"]
    assert fm["program_id"] == new_id
    assert fm["status"] == "active"
    assert fm["type"] == "roadmap-initiative"
    assert fm["title"] == "Smart reconciliation"
    # First phase of roadmap-initiative is `discovery`.
    assert fm["phase"] == "discovery"
    # The newborn's first phase is stamped with an entry date so it can be aged.
    assert fm.get("phase_entered")
    # owner_role defaults to a role token (never a name).
    assert fm["owner_role"] == "product"
    # Carried checkpoint, forced to status pending.
    assert len(fm["checkpoints"]) == 1
    cp = fm["checkpoints"][0]
    assert cp["id"] == "discovery-exit"
    assert cp["status"] == "pending"
    # Exactly one origin observation, stamped by the intake sentinel.
    obs = list(pl.iter_observations(prog["body"]))
    assert len(obs) == 1
    date, kind, sentinel, source, claim = obs[0]
    assert kind == "status-signal"
    assert sentinel == "program-intake"
    assert source == "meeting-A"  # first citation
    assert claim  # non-empty
    # Intent is non-empty and carries the citations.
    intent = pl._parse_intent(prog["body"])
    assert intent
    assert "meeting-A" in intent and "meeting-B" in intent


def test_birth_program_unknown_type_raises(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        pl.birth_program(
            {"program_type": "not-a-real-type", "title": "Nope"},
            root=str(tmp_path),
        )


def test_birth_program_blank_title_raises_birth_specific(tmp_path):
    # A missing/blank title fails with the birth-specific message rather than
    # surfacing from deep inside create_program.
    import pytest
    with pytest.raises(ValueError, match="birth spec requires a title"):
        pl.birth_program(
            {"program_type": "roadmap-initiative", "title": "   "},
            root=str(tmp_path),
        )
    with pytest.raises(ValueError, match="birth spec requires a title"):
        pl.birth_program(
            {"program_type": "roadmap-initiative"},
            root=str(tmp_path),
        )


def test_birth_program_returns_freshly_minted_id(tmp_path):
    root = str(tmp_path)
    new_id = pl.birth_program(
        {"program_type": "roadmap-initiative", "title": "Fresh one"},
        root=root,
    )
    # The returned id round-trips through read_program.
    prog = pl.read_program(new_id, root=root)
    assert prog["frontmatter"]["program_id"] == new_id
    # A second birth mints a distinct id.
    second = pl.birth_program(
        {"program_type": "roadmap-initiative", "title": "Second one"},
        root=root,
    )
    assert second != new_id


def test_birth_program_citations_land_in_intent(tmp_path):
    root = str(tmp_path)
    new_id = pl.birth_program(
        {
            "program_type": "roadmap-initiative",
            "title": "Cited birth",
            "citations": ["GONG-123", "ZD-456"],
        },
        root=root,
    )
    intent = pl._parse_intent(pl.read_program(new_id, root=root)["body"])
    assert "GONG-123" in intent
    assert "ZD-456" in intent


def test_birth_program_register_type_does_not_crash_on_phase(tmp_path):
    # A non-pipeline (register / cycle) type has no phases; birth must not
    # try to infer a phase or crash.
    root = str(tmp_path)
    new_id = pl.birth_program(
        {"program_type": "program-intake", "title": "Nursery 2"},
        root=root,
    )
    fm = pl.read_program(new_id, root=root)["frontmatter"]
    assert fm["status"] == "active"
    assert fm["type"] == "program-intake"
    # No phase inferred for a register-model type.
    assert fm.get("phase") is None
    assert fm["checkpoints"] == []


def test_birth_program_owner_role_from_spec(tmp_path):
    root = str(tmp_path)
    new_id = pl.birth_program(
        {
            "program_type": "roadmap-initiative",
            "title": "Owned",
            "owner_role": "engineering",
        },
        root=root,
    )
    assert pl.read_program(new_id, root=root)["frontmatter"]["owner_role"] == "engineering"


def test_birth_program_empty_citations_still_births(tmp_path):
    # No citations at all -> Intent + observation still land (source falls back).
    root = str(tmp_path)
    new_id = pl.birth_program(
        {"program_type": "roadmap-initiative", "title": "Uncited"},
        root=root,
    )
    prog = pl.read_program(new_id, root=root)
    obs = list(pl.iter_observations(prog["body"]))
    assert len(obs) == 1
    assert obs[0][3] == "intake"  # source falls back to "intake"


def test_render_view_projects_intake_candidates_with_extra_fields(tmp_path):
    # Task 8: render_view on a program-intake program surfaces its candidates
    # with the mapped fields: name <- title, owner <- program_type,
    # age <- source_count, and status + possible_duplicate_of.
    root = str(tmp_path)
    reg = pl.load_registry()
    ip = _seed_intake(tmp_path)

    # Upsert a candidate: two sources.
    pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-A", claim="Mentioned.",
        root=root)
    pl.upsert_candidate(
        ip, candidate_key="k1", program_type="roadmap-initiative",
        title="Smart reconciliation", source="meeting-B", claim="Raised again.",
        root=root)

    # Upsert a flagged candidate (possible duplicate).
    pl.upsert_candidate(
        ip, candidate_key="k2", program_type="eos-rock",
        title="Revenue sync", source="meeting-C", claim="Epic ROCK idea.",
        link_to="CAND-0001", confidence=0.5, root=root)

    prog = pl.read_program(ip, root=root)
    vm = pl.render_view(prog, reg)

    assert vm["model"] == "register"
    assert len(vm["items"]) == 2

    # First candidate: 2 sources, no duplicate marker.
    item0 = vm["items"][0]
    assert item0["name"] == "Smart reconciliation"
    assert item0["owner"] == "roadmap-initiative"
    assert item0["age"] == 2
    assert item0["status"] == "open"
    assert item0.get("possible_duplicate_of") is None

    # Second candidate: 1 source, marked as possible duplicate.
    item1 = vm["items"][1]
    assert item1["name"] == "Revenue sync"
    assert item1["owner"] == "eos-rock"
    assert item1["age"] == 1
    assert item1["status"] == "open"
    assert item1["possible_duplicate_of"] == "CAND-0001"


# ─── archive mutation op (Task 2) ──────────────────────────────────────────────

def test_apply_archive_moves_file_and_sets_status(tmp_path):
    """Archive moves program to archive/, sets status, and appends observation."""
    root = str(tmp_path)
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="To archive", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})

    result = pl.apply_mutation(pid, {
        "op": "archive",
        "reason": "terminal phase reached",
        "citations": ["meeting:X"]
    }, root=root)

    # Check return value
    assert result["applied"] == "archive"
    assert result["program_id"] == pid
    assert "to" in result
    assert result["to"].startswith(f"archive/{pid}")

    # Check file no longer exists at active path
    active_path = os.path.join(pl._program_dir(root), f"{pid}.md")
    assert not os.path.isfile(active_path)

    # Check file exists at archive path
    archive_path = os.path.join(pl._program_dir(root), result["to"])
    assert os.path.isfile(archive_path)

    # Check frontmatter has status == "archived"
    prog = pl.read_program(pid, root=root)
    assert prog["frontmatter"]["status"] == "archived"

    # Check body has appended completion observation
    obs_list = list(pl.iter_observations(prog["body"]))
    assert len(obs_list) > 0
    # The last observation should be the completion entry
    last_obs = obs_list[-1]
    date, kind, sentinel, source, claim = last_obs
    assert kind == "completion"
    assert sentinel == "reconciler"
    assert "archived" in claim.lower()


def test_apply_archive_version_suffixes_on_collision(tmp_path):
    """Archive with collision creates versioned filename."""
    root = str(tmp_path)
    archive_dir = os.path.join(pl._program_dir(root), "archive")
    os.makedirs(archive_dir, exist_ok=True)

    # Pre-create archive/<pid>.md
    existing_archive = os.path.join(archive_dir, "PROG-0001.md")
    with open(existing_archive, "w") as f:
        f.write("---\nstatus: archived\n---\nPre-existing archive\n")

    # Create a fresh active program with same pid (by manually creating it)
    # Since create_program uses _next_id, we need to seed the counter
    counter_path = pl._counter_path(root)
    os.makedirs(pl._program_dir(root), exist_ok=True)
    with open(counter_path, "w") as f:
        f.write("1")  # Next id will be PROG-0001

    pid, _ = pl.create_program(
        type="roadmap-initiative", title="New one", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})
    assert pid == "PROG-0001"

    # Archive it
    result = pl.apply_mutation(pid, {
        "op": "archive",
        "reason": "done",
        "citations": []
    }, root=root)

    # Check it lands at -v2
    assert result["to"] == f"archive/{pid}-v2.md"
    assert os.path.isfile(os.path.join(archive_dir, f"{pid}-v2.md"))

    # Check original is unchanged
    with open(existing_archive) as f:
        content = f.read()
    assert "Pre-existing archive" in content


def test_apply_archive_idempotent_when_already_archived(tmp_path):
    """Archive twice on same pid is idempotent (no -v2 created)."""
    root = str(tmp_path)
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="To archive twice", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})

    # Archive once
    result1 = pl.apply_mutation(pid, {
        "op": "archive",
        "reason": "done",
        "citations": []
    }, root=root)
    assert result1["applied"] == "archive"

    # Archive again (should be read from archive)
    result2 = pl.apply_mutation(pid, {
        "op": "archive",
        "reason": "done again",
        "citations": []
    }, root=root)

    # Should be noop/success
    assert result2["applied"] is None
    assert result2["status"] == "noop"

    # Check no -v2 was created
    archive_dir = os.path.join(pl._program_dir(root), "archive")
    files = os.listdir(archive_dir)
    versioned = [f for f in files if "-v2" in f]
    assert len(versioned) == 0

    # Check only ONE observation entry (not appended twice)
    prog = pl.read_program(pid, root=root)
    obs_list = list(pl.iter_observations(prog["body"]))
    assert len(obs_list) == 1


def test_read_program_resolves_archived_file(tmp_path):
    """read_program finds archived files and returns them."""
    root = str(tmp_path)
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="To find in archive", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})

    # Archive it
    pl.apply_mutation(pid, {
        "op": "archive",
        "reason": "done",
        "citations": []
    }, root=root)

    # Read it back (should find in archive)
    prog = pl.read_program(pid, root=root)
    assert prog["frontmatter"]["status"] == "archived"
    assert prog["frontmatter"]["title"] == "To find in archive"


def test_apply_archive_defaults_reason_when_missing(tmp_path):
    """Archive without reason defaults to 'archived' (does NOT refuse)."""
    root = str(tmp_path)
    pid, _ = pl.create_program(
        type="roadmap-initiative", title="No reason", owner_role="product",
        root=root, frontmatter_extra={"phase": "execution", "drift": "holding"})

    # Archive with no reason
    result = pl.apply_mutation(pid, {
        "op": "archive"
        # no reason field
    }, root=root)

    # Should still archive (reason defaults)
    assert result["applied"] == "archive"
    prog = pl.read_program(pid, root=root)
    assert prog["frontmatter"]["status"] == "archived"
