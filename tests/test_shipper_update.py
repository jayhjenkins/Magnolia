import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import shipper


def test_attempt_update_no_block():
    """_attempt_update with None update returns error 400."""
    status, payload = shipper._attempt_update("T-1", None)
    assert status == "error"
    assert payload == (400, "No JIRA_UPDATE block found in task body")


def test_attempt_update_already_done(monkeypatch):
    """_attempt_update skips when task status is done."""
    monkeypatch.setattr(shipper.task_lib, "read_task",
                        lambda tid: {"frontmatter": {"status": "done"}, "body": ""})
    monkeypatch.setattr(shipper.task_lib, "update_task",
                        lambda tid, **kw: None)
    status, payload = shipper._attempt_update("T-1", {"issue_key": "VNT-1", "action": "comment"})
    assert status == "already_updated"
    assert payload is None


def test_attempt_update_success(monkeypatch):
    """_attempt_update with valid update calls adapter and marks task done."""
    completed = {}
    monkeypatch.setattr(shipper.task_lib, "read_task",
                        lambda tid: {"frontmatter": {"status": "open"}, "body": ""})
    monkeypatch.setattr(shipper.adapters, "update_issue",
                        lambda family, update, root=None: ("VNT-100", "https://jira/VNT-100"))
    monkeypatch.setattr(shipper.task_lib, "update_task",
                        lambda tid, **kw: None)
    monkeypatch.setattr(shipper.task_lib, "complete_task",
                        lambda tid, **kw: completed.setdefault("id", tid))
    monkeypatch.setattr(shipper.jira_publish, "_trace_update",
                        lambda *a, **kw: None)
    update = {"issue_key": "VNT-100", "action": "comment"}
    status, payload = shipper._attempt_update("T-1", update)
    assert status == "ok"
    assert payload == ("VNT-100", "https://jira/VNT-100")
    assert completed["id"] == "T-1"


def test_emit_jira_receipt_creates_card(monkeypatch):
    """_emit_jira_receipt creates a collab receipt card with Jira link."""
    created = {}
    updated = {}
    completed = {}
    monkeypatch.setattr(shipper.task_lib, "read_task",
                        lambda tid: {"frontmatter": {"title": "My Task"}})
    monkeypatch.setattr(shipper.task_lib, "create_task",
                        lambda *a, **kw: (created.setdefault("id", "TASK-99"), None))
    def track_update(tid, **kw):
        updated[tid] = kw.get("changes", {})
    monkeypatch.setattr(shipper.task_lib, "update_task", track_update)
    monkeypatch.setattr(shipper.task_lib, "complete_task",
                        lambda tid, **kw: completed.setdefault("id", tid))
    receipt_id = shipper._emit_jira_receipt("TASK-10", "VNT-100", "https://jira/VNT-100", "Created")
    assert receipt_id == "TASK-99"
    assert "TASK-99" in updated
    assert updated["TASK-99"]["receipt_kind"] == "jira"
    assert updated["TASK-99"]["issue_key"] == "VNT-100"
    assert updated["TASK-99"]["issue_url"] == "https://jira/VNT-100"
    assert completed["id"] == "TASK-99"


def test_emit_jira_receipt_includes_source_title(monkeypatch):
    """_emit_jira_receipt includes source task title in description."""
    created_title = None
    created_desc = None
    monkeypatch.setattr(shipper.task_lib, "read_task",
                        lambda tid: {"frontmatter": {"title": "Original Request"}})
    def track_create(title, *a, **kw):
        nonlocal created_title, created_desc
        created_title = title
        created_desc = kw.get("description", "")
        return ("TASK-99", None)
    monkeypatch.setattr(shipper.task_lib, "create_task", track_create)
    monkeypatch.setattr(shipper.task_lib, "update_task",
                        lambda *a, **kw: None)
    monkeypatch.setattr(shipper.task_lib, "complete_task",
                        lambda *a, **kw: None)
    shipper._emit_jira_receipt("TASK-10", "VNT-100", "https://jira/VNT-100", "Updated")
    assert "Original Request" in created_desc
    assert "VNT-100" in created_title
