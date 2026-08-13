import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import shipper
import jira_publish


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


# ─── Pre-transition drift verification ────────────────────────────────────


def test_drift_resolved_skips_transition(monkeypatch):
    """When Jira status has drifted from expected, auto-complete instead of
    executing the transition."""
    completed = {}
    noted = {}
    monkeypatch.setattr(shipper.task_lib, "read_task",
                        lambda tid: {"frontmatter": {"status": "open"}, "body": ""})
    monkeypatch.setattr(shipper.task_lib, "update_task",
                        lambda tid, **kw: noted.update(kw))
    monkeypatch.setattr(shipper.task_lib, "complete_task",
                        lambda tid, **kw: completed.setdefault("id", tid))
    monkeypatch.setattr(shipper.jira_publish, "fetch_issue",
                        lambda key: {"status": "In Progress", "title": "Test"})
    update = {
        "issue_key": "VNT-100",
        "action": "transition",
        "target_status": "In Progress",
        "expected_status": "Next",
    }
    status, payload = shipper._attempt_update("T-1", update)
    assert status == "drift_resolved"
    assert "In Progress" in payload
    assert "Next" in payload
    assert completed["id"] == "T-1"


def test_no_drift_proceeds_with_transition(monkeypatch):
    """When Jira status still matches expected, proceed with the transition."""
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
    monkeypatch.setattr(shipper.jira_publish, "fetch_issue",
                        lambda key: {"status": "Next", "title": "Test"})
    update = {
        "issue_key": "VNT-100",
        "action": "transition",
        "target_status": "In Progress",
        "expected_status": "Next",
    }
    status, payload = shipper._attempt_update("T-1", update)
    assert status == "ok"
    assert payload == ("VNT-100", "https://jira/VNT-100")


def test_drift_check_fails_open(monkeypatch):
    """When fetch_issue raises, the transition proceeds (fail-open)."""
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
    def fetch_boom(key):
        raise RuntimeError("Jira unreachable")
    monkeypatch.setattr(shipper.jira_publish, "fetch_issue", fetch_boom)
    update = {
        "issue_key": "VNT-100",
        "action": "transition",
        "target_status": "In Progress",
        "expected_status": "Next",
    }
    status, payload = shipper._attempt_update("T-1", update)
    assert status == "ok"


def test_no_expected_status_skips_drift_check(monkeypatch):
    """When expected_status is absent, no drift check happens."""
    completed = {}
    fetch_calls = []
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
    def fetch_track(key):
        fetch_calls.append(key)
        return {"status": "Done", "title": "Test"}
    monkeypatch.setattr(shipper.jira_publish, "fetch_issue", fetch_track)
    update = {
        "issue_key": "VNT-100",
        "action": "transition",
        "target_status": "In Progress",
    }
    status, payload = shipper._attempt_update("T-1", update)
    assert status == "ok"
    assert fetch_calls == []


# ─── parse_jira_update expected_status ─────────────────────────────────────


def test_parse_jira_update_expected_status():
    """parse_jira_update extracts JIRA_EXPECTED_STATUS when present."""
    body = (
        "<!-- JIRA_UPDATE -->\n"
        "<!-- JIRA_ISSUE_KEY:VNT-100 -->\n"
        "<!-- JIRA_ACTION:transition -->\n"
        "<!-- JIRA_TARGET_STATUS:In Progress -->\n"
        "<!-- JIRA_EXPECTED_STATUS:Next -->\n"
        "<!-- JIRA_PRIORITY: -->\n"
        "<!-- JIRA_SUMMARY: -->\n"
        "<!-- JIRA_LABELS: -->\n\n"
        "### Comment\nTransition comment.\n"
        "<!-- /JIRA_UPDATE -->"
    )
    result = jira_publish.parse_jira_update(body)
    assert result is not None
    assert result["expected_status"] == "Next"
    assert result["target_status"] == "In Progress"


def test_parse_jira_update_no_expected_status():
    """parse_jira_update returns empty expected_status for legacy blocks."""
    body = (
        "<!-- JIRA_UPDATE -->\n"
        "<!-- JIRA_ISSUE_KEY:VNT-100 -->\n"
        "<!-- JIRA_ACTION:comment -->\n"
        "<!-- JIRA_PRIORITY: -->\n"
        "<!-- JIRA_SUMMARY: -->\n"
        "<!-- JIRA_LABELS: -->\n\n"
        "### Comment\nSome comment.\n"
        "<!-- /JIRA_UPDATE -->"
    )
    result = jira_publish.parse_jira_update(body)
    assert result is not None
    assert result["expected_status"] == ""
