"""Tests that jira_publish CLI argv includes --permission-mode bypassPermissions.

This is the regression guard: the harness refactor silently dropped permission
bypass, causing Claude to refuse MCP tool calls in headless publish sessions.
These tests catch that at the argv level so it never regresses again.
"""
import subprocess
import pytest


@pytest.fixture
def jp(monkeypatch):
    """Import jira_publish with module-level Jira constants patched."""
    import jira_publish
    monkeypatch.setattr(jira_publish, "PM_OS_DIR", "/tmp/fake")
    monkeypatch.setattr(jira_publish, "JIRA_CLOUD_ID", "test-cloud-id")
    monkeypatch.setattr(jira_publish, "JIRA_PROJECT_KEY", "TEST")
    monkeypatch.setattr(jira_publish, "JIRA_COMPONENT_ID", "999")
    monkeypatch.setattr(jira_publish, "JIRA_AUTO_LABEL", "test_label")
    monkeypatch.setattr(jira_publish, "JIRA_DEFAULT_ASSIGNEE", "acct-123")
    monkeypatch.setattr(jira_publish, "JIRA_BROWSE_BASE",
                        "https://test.atlassian.net/browse")
    return jira_publish


def _capture_argv(monkeypatch, jp, stdout_text):
    """Monkeypatch subprocess.run in jira_publish to capture argv."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["argv"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0,
                                           stdout=stdout_text, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


class TestRunJiraSessionArgv:
    def test_publish_argv_includes_permission_bypass(self, jp, monkeypatch):
        captured = _capture_argv(
            monkeypatch, jp,
            '{"result":"JIRA_RESULT:TEST-1|https://x/TEST-1"}')
        jp._run_jira_session(
            "Create this issue...",
            "mcp__claude_ai_Jira__createJiraIssue",
        )
        argv = captured["argv"]
        assert "--permission-mode" in argv, \
            f"--permission-mode missing from publish argv: {argv}"
        idx = argv.index("--permission-mode")
        assert argv[idx + 1] == "bypassPermissions"

    def test_publish_argv_includes_append_system_prompt(self, jp, monkeypatch):
        captured = _capture_argv(
            monkeypatch, jp,
            '{"result":"JIRA_RESULT:TEST-1|https://x/TEST-1"}')
        jp._run_jira_session(
            "Create this issue...",
            "mcp__claude_ai_Jira__createJiraIssue",
        )
        argv = captured["argv"]
        assert "--append-system-prompt" in argv
        idx = argv.index("--append-system-prompt")
        assert "mechanical" in argv[idx + 1].lower()

    def test_publish_argv_includes_allowed_tools(self, jp, monkeypatch):
        captured = _capture_argv(
            monkeypatch, jp,
            '{"result":"JIRA_RESULT:TEST-1|https://x/TEST-1"}')
        jp._run_jira_session(
            "Create this issue...",
            "mcp__claude_ai_Jira__createJiraIssue",
        )
        argv = captured["argv"]
        assert "--allowedTools" in argv
        idx = argv.index("--allowedTools")
        assert "mcp__claude_ai_Jira__createJiraIssue" in argv[idx + 1]

    def test_transition_argv_includes_permission_bypass(self, jp, monkeypatch):
        captured = _capture_argv(
            monkeypatch, jp,
            '{"result":"JIRA_RESULT:TEST-1|https://x/TEST-1"}')
        tools = ",".join([
            "mcp__claude_ai_Jira__getTransitionsForJiraIssue",
            "mcp__claude_ai_Jira__transitionJiraIssue",
        ])
        jp._run_jira_session("Transition...", tools, max_turns=5)
        argv = captured["argv"]
        assert "--permission-mode" in argv
        idx = argv.index("--permission-mode")
        assert argv[idx + 1] == "bypassPermissions"


class TestRunJiraReadSessionArgv:
    def test_read_argv_includes_permission_bypass(self, jp, monkeypatch):
        captured = _capture_argv(
            monkeypatch, jp,
            '{"result":"JIRA_READ:In Progress|Test|2026-09-01|none|none"}')
        jp._run_jira_read_session("TEST-42")
        argv = captured["argv"]
        assert "--permission-mode" in argv, \
            f"--permission-mode missing from read argv: {argv}"
        idx = argv.index("--permission-mode")
        assert argv[idx + 1] == "bypassPermissions"

    def test_read_argv_includes_get_issue_tool(self, jp, monkeypatch):
        captured = _capture_argv(
            monkeypatch, jp,
            '{"result":"JIRA_READ:Done|Test|none|none|none"}')
        jp._run_jira_read_session("TEST-42")
        argv = captured["argv"]
        assert "--allowedTools" in argv
        idx = argv.index("--allowedTools")
        assert "getJiraIssue" in argv[idx + 1]
