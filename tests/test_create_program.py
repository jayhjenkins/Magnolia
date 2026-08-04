"""Tests for handle_create_program: programs created via program-setup cards.

Verifies that pipeline programs get phase/phase_entered/drift/checkpoints,
and that tracker keys are extracted and set as bindings.
"""
import types

import pytest

import program_lib
import task_lib
import task_server


def _pin_programs(tmp_path, monkeypatch):
    pdir = tmp_path / "datasets" / "programs"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(program_lib, "_program_dir", lambda root=None: str(pdir))
    monkeypatch.setattr(
        program_lib, "_counter_path", lambda root=None: str(pdir / "_counter"))


class FakeHandler:
    """Minimal HTTP handler stub for handle_create_program."""

    def __init__(self):
        self.response_code = None
        self.response_body = None

    def send_response(self, code):
        self.response_code = code

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass

    @property
    def wfile(self):
        import io
        return io.BytesIO()


def _create_setup_card(program_type, title, body="", tracker_key=None):
    """Create a program-setup card and return its task id."""
    tid, _ = task_lib.create_task(
        f"Set up: {title}", queue="human", creator="agent",
        card_type="program-setup",
        description=body)
    changes = {"program_type": program_type}
    if tracker_key:
        changes["tracker_key"] = tracker_key
    task_lib.update_task(tid, changes=changes)
    return tid


def test_pipeline_type_gets_phase_and_drift(tmp_path, tasks_root, monkeypatch):
    _pin_programs(tmp_path, monkeypatch)
    tid = _create_setup_card("roadmap-initiative", "Alpha launch")

    handler = FakeHandler()
    task_server.handle_create_program(handler, tid)

    progs = program_lib.list_programs(status="active")
    assert len(progs) == 1
    fm = progs[0]["frontmatter"]
    assert fm["phase"] == "discovery"
    assert fm.get("phase_entered")
    assert fm["drift"] == "holding"
    assert fm["checkpoints"] == []


def test_non_pipeline_type_has_no_phase(tmp_path, tasks_root, monkeypatch):
    _pin_programs(tmp_path, monkeypatch)
    tid = _create_setup_card("weekly-priorities", "Weekly priorities")

    handler = FakeHandler()
    task_server.handle_create_program(handler, tid)

    progs = program_lib.list_programs(status="active")
    assert len(progs) == 1
    fm = progs[0]["frontmatter"]
    assert "phase" not in fm
    assert fm["drift"] == "holding"


def test_tracker_key_in_frontmatter_sets_bindings(tmp_path, tasks_root, monkeypatch):
    _pin_programs(tmp_path, monkeypatch)
    tid = _create_setup_card("roadmap-initiative", "Alpha launch",
                             tracker_key="VNT-45655")

    handler = FakeHandler()
    task_server.handle_create_program(handler, tid)

    progs = program_lib.list_programs(status="active")
    fm = progs[0]["frontmatter"]
    assert fm["bindings"] == [
        {"role": "truth", "kind": "project_management", "anchor": "VNT-45655"}
    ]


def test_tracker_key_extracted_from_body(tmp_path, tasks_root, monkeypatch):
    _pin_programs(tmp_path, monkeypatch)
    body = "## Intent\nTracker: VNT-12345. Build the new feed."
    tid = _create_setup_card("roadmap-initiative", "Alpha launch", body=body)

    handler = FakeHandler()
    task_server.handle_create_program(handler, tid)

    progs = program_lib.list_programs(status="active")
    fm = progs[0]["frontmatter"]
    bindings = fm.get("bindings") or []
    assert any(b["anchor"] == "VNT-12345" for b in bindings)


def test_no_tracker_key_means_no_bindings(tmp_path, tasks_root, monkeypatch):
    _pin_programs(tmp_path, monkeypatch)
    tid = _create_setup_card("roadmap-initiative", "Alpha launch",
                             body="No jira key here")

    handler = FakeHandler()
    task_server.handle_create_program(handler, tid)

    progs = program_lib.list_programs(status="active")
    fm = progs[0]["frontmatter"]
    assert "bindings" not in fm


# --- _extract_tracker_key unit tests ----------------------------------------

def test_extract_tracker_key_finds_jira_key():
    assert task_server._extract_tracker_key("Tracker: VNT-42411") == "VNT-42411"


def test_extract_tracker_key_returns_none_for_no_key():
    assert task_server._extract_tracker_key("No key here") is None


def test_extract_tracker_key_returns_none_for_empty():
    assert task_server._extract_tracker_key("") is None
    assert task_server._extract_tracker_key(None) is None


def test_extract_tracker_key_finds_first_key():
    assert task_server._extract_tracker_key("VNT-100 and VNT-200") == "VNT-100"
