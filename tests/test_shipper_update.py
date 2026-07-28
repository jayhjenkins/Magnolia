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
