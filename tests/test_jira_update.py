# All tests are pure — no subprocess, no Jira MCP calls.
# They test parse_jira_update and the prompt builders.

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import jira_publish


def test_parse_jira_update_comment():
    body = """Some text before
<!-- JIRA_UPDATE -->
<!-- JIRA_ISSUE_KEY:VNT-45655 -->
<!-- JIRA_ACTION:comment -->

### Comment
Bug details from email thread. Invoice totals not subtracting credits.

<!-- /JIRA_UPDATE -->
More text after"""
    u = jira_publish.parse_jira_update(body)
    assert u is not None
    assert u["issue_key"] == "VNT-45655"
    assert u["action"] == "comment"
    assert "Invoice totals" in u["comment_body"]
    assert u["description"] == ""


def test_parse_jira_update_edit():
    body = """<!-- JIRA_UPDATE -->
<!-- JIRA_ISSUE_KEY:VNT-12345 -->
<!-- JIRA_ACTION:edit -->
<!-- JIRA_PRIORITY:High -->
<!-- JIRA_SUMMARY:Updated title here -->
<!-- JIRA_LABELS:home_aidlc -->

### Description
Updated description with full details.

<!-- /JIRA_UPDATE -->"""
    u = jira_publish.parse_jira_update(body)
    assert u is not None
    assert u["issue_key"] == "VNT-12345"
    assert u["action"] == "edit"
    assert u["priority"] == "High"
    assert u["summary"] == "Updated title here"
    assert u["labels"] == ["home_aidlc"]
    assert "Updated description" in u["description"]
    assert u["comment_body"] == ""


def test_parse_jira_update_comment_and_edit():
    body = """<!-- JIRA_UPDATE -->
<!-- JIRA_ISSUE_KEY:VNT-99999 -->
<!-- JIRA_ACTION:comment_and_edit -->
<!-- JIRA_PRIORITY:Highest -->

### Comment
Adding severity assessment.

### Description
Full bug report with repro steps.

<!-- /JIRA_UPDATE -->"""
    u = jira_publish.parse_jira_update(body)
    assert u is not None
    assert u["issue_key"] == "VNT-99999"
    assert u["action"] == "comment_and_edit"
    assert u["priority"] == "Highest"
    assert "severity assessment" in u["comment_body"]
    assert "repro steps" in u["description"]


def test_parse_jira_update_missing_issue_key():
    body = """<!-- JIRA_UPDATE -->
<!-- JIRA_ACTION:comment -->

### Comment
Some comment.

<!-- /JIRA_UPDATE -->"""
    assert jira_publish.parse_jira_update(body) is None


def test_parse_jira_update_no_block():
    assert jira_publish.parse_jira_update("just regular text") is None
    assert jira_publish.parse_jira_update("") is None
    assert jira_publish.parse_jira_update(None) is None


def test_parse_jira_update_defaults_action_to_comment():
    body = """<!-- JIRA_UPDATE -->
<!-- JIRA_ISSUE_KEY:VNT-100 -->

### Comment
A comment without explicit action.

<!-- /JIRA_UPDATE -->"""
    u = jira_publish.parse_jira_update(body)
    assert u["action"] == "comment"


def test_build_comment_prompt_targets_add_comment(monkeypatch):
    monkeypatch.setattr(jira_publish, "JIRA_CLOUD_ID", "acme.atlassian.net")
    monkeypatch.setattr(jira_publish, "JIRA_BROWSE_BASE", "https://acme.atlassian.net/browse")
    update = {"issue_key": "ACM-123", "comment_body": "Test comment body"}
    prompt = jira_publish.build_comment_prompt(update)
    assert "addCommentToJiraIssue" in prompt
    assert "ACM-123" in prompt
    assert "Test comment body" in prompt
    assert "acme.atlassian.net" in prompt


def test_build_edit_prompt_targets_edit_issue(monkeypatch):
    monkeypatch.setattr(jira_publish, "JIRA_CLOUD_ID", "acme.atlassian.net")
    monkeypatch.setattr(jira_publish, "JIRA_BROWSE_BASE", "https://acme.atlassian.net/browse")
    update = {
        "issue_key": "ACM-456",
        "summary": "New title",
        "priority": "High",
        "labels": ["label1"],
        "description": "New description",
    }
    prompt = jira_publish.build_edit_prompt(update)
    assert "editJiraIssue" in prompt
    assert "ACM-456" in prompt
    assert "New title" in prompt
    assert "acme.atlassian.net" in prompt


def test_build_edit_prompt_omits_empty_fields(monkeypatch):
    monkeypatch.setattr(jira_publish, "JIRA_CLOUD_ID", "acme.atlassian.net")
    monkeypatch.setattr(jira_publish, "JIRA_BROWSE_BASE", "https://acme.atlassian.net/browse")
    update = {
        "issue_key": "ACM-789",
        "summary": "",
        "priority": "",
        "labels": [],
        "description": "Only description changed",
    }
    prompt = jira_publish.build_edit_prompt(update)
    assert "editJiraIssue" in prompt
    assert '"summary"' not in prompt
    assert '"priority"' not in prompt
    assert "Only description changed" in prompt
