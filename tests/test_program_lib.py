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


def test_render_view_surfaces_items_for_cycle(tmp_path):
    # A cycle program (weekly-priorities) can declare `items` — the week's
    # priorities. render_view must surface them in the view model, mirroring the
    # register branch, so the Cadence row can list them.
    root = str(tmp_path)
    reg = pl.load_registry()
    pid, _ = pl.create_program(
        type="weekly-priorities", title="Weekly priorities", owner_role="product",
        root=root, frontmatter_extra={
            "drift": "holding",
            "status_line": "Sent Monday - 9 of 9 done",
            "items": [
                {"name": "Close payments PRD", "owner": "product", "age": 2},
                {"name": "Review home backlog", "owner": "product", "age": 5},
            ],
        })
    vm = pl.render_view(pl.read_program(pid, root=root), reg)
    assert vm["model"] == "cycle"
    assert len(vm["items"]) == 2
    assert vm["items"][0]["name"] == "Close payments PRD"
    assert vm["items"][1]["owner"] == "product"
    assert vm["items"][1]["age"] == 5


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
    assert len(progs) == 13     # concrete count: a silently-dropped/malformed seed fails here
    for p in progs:
        vm = pl.render_view(p, reg)
        assert vm["model"] in {"pipeline", "target", "cycle", "register"}
        assert vm["name"]
    # the family payload groups them and drops no expected family
    payload = pl.build_cadence_payload()
    fam_ids = [f["id"] for f in payload["families"]]
    assert fam_ids == ["roadmap", "weekly", "outcomes", "eos"]


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
