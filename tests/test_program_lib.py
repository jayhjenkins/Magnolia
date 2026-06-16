import os

import program_lib as pl


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
