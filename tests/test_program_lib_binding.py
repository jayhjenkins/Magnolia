"""Tests for program_lib.set_binding and birth-path binding propagation."""
import program_lib


def _pin(tmp_path, monkeypatch):
    pdir = tmp_path / "datasets" / "programs"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(program_lib, "_program_dir", lambda root=None: str(pdir))
    monkeypatch.setattr(
        program_lib, "_counter_path", lambda root=None: str(pdir / "_counter"))


def test_set_binding_adds_new_binding(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    pid, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product")
    result = program_lib.set_binding(pid, "truth", "project_management", "VNT-123")
    assert result is True
    fm = program_lib.read_program(pid)["frontmatter"]
    assert fm["bindings"] == [
        {"role": "truth", "kind": "project_management", "anchor": "VNT-123"}
    ]


def test_set_binding_idempotent(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    pid, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product")
    program_lib.set_binding(pid, "truth", "project_management", "VNT-123")
    result = program_lib.set_binding(pid, "truth", "project_management", "VNT-123")
    assert result is False
    fm = program_lib.read_program(pid)["frontmatter"]
    assert len(fm["bindings"]) == 1


def test_set_binding_updates_existing_anchor(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    pid, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product",
        frontmatter_extra={"bindings": [
            {"role": "truth", "kind": "project_management", "anchor": "OLD-1"}
        ]})
    result = program_lib.set_binding(pid, "truth", "project_management", "NEW-2")
    assert result is True
    fm = program_lib.read_program(pid)["frontmatter"]
    assert len(fm["bindings"]) == 1
    assert fm["bindings"][0]["anchor"] == "NEW-2"


def test_set_binding_preserves_other_bindings(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    pid, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product",
        frontmatter_extra={"bindings": [
            {"role": "surface", "kind": "eos_sheet", "anchor": "sheet-1"}
        ]})
    program_lib.set_binding(pid, "truth", "project_management", "VNT-123")
    fm = program_lib.read_program(pid)["frontmatter"]
    assert len(fm["bindings"]) == 2


def test_birth_program_with_anchor_sets_binding(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    pid = program_lib.birth_program({
        "program_type": "roadmap-initiative",
        "title": "Alpha launch",
        "anchor": "EPIC-204",
        "citations": ["meeting:2026-07-01"],
    })
    fm = program_lib.read_program(pid)["frontmatter"]
    bindings = fm.get("bindings") or []
    assert any(
        b.get("role") == "truth"
        and b.get("kind") == "project_management"
        and b.get("anchor") == "EPIC-204"
        for b in bindings
    )


def test_birth_program_without_anchor_has_no_binding(tmp_path, monkeypatch):
    _pin(tmp_path, monkeypatch)
    pid = program_lib.birth_program({
        "program_type": "roadmap-initiative",
        "title": "Beta launch",
        "citations": ["meeting:2026-07-02"],
    })
    fm = program_lib.read_program(pid)["frontmatter"]
    bindings = fm.get("bindings") or []
    assert not any(b.get("kind") == "project_management" for b in bindings)
