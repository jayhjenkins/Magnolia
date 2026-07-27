"""Test judge's gather_evidence for send-message tasks with message_body field.

This test verifies that the judge correctly prioritizes the message_body field
from the task frontmatter when collecting evidence for message-kind tasks.
Before the fix, send-message tasks would fail to find drafted messages because
agent_output (file path) is empty for send-message cards, and the judge would
only find the task description/activity log, scoring them 1/10.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from judge import gather_evidence


def test_gather_evidence_message_prioritizes_message_body():
    """message_body in frontmatter takes priority over artifact file."""
    fm = {
        "message_body": "Teams: Quick standup moved to 10am\n\nEmail: Hi team, the standup is now at 10am.",
    }
    body = "Task description text that should be ignored"

    evidence, note = gather_evidence("message", fm, body, "TASK-TEST-1")

    assert "Quick standup moved to 10am" in evidence
    assert "message_body" in note
    assert len(evidence) > 0


def test_gather_evidence_message_falls_back_to_artifact():
    """Fallback to artifact file when message_body is missing or empty."""
    fm = {
        "message_body": "",  # Empty message_body
        "agent_output": None,  # No artifact file
    }
    body = "This is the task body text with the actual message"

    evidence, note = gather_evidence("message", fm, body, "TASK-TEST-2")

    assert "task body text" in evidence
    assert "task body" in note


def test_gather_evidence_message_empty_returns_none():
    """Return None when no message text is found anywhere."""
    fm = {
        "message_body": None,
        "agent_output": None,
    }
    body = ""

    evidence, note = gather_evidence("message", fm, body, "TASK-TEST-3")

    assert evidence is None
    assert "no message text found" in note


def test_gather_evidence_message_strips_whitespace():
    """message_body is stripped of leading/trailing whitespace."""
    fm = {
        "message_body": "  \n  Actual message  \n  ",
    }
    body = "ignored"

    evidence, note = gather_evidence("message", fm, body, "TASK-TEST-4")

    assert evidence == "Actual message"
    assert not evidence.startswith(" ")
    assert not evidence.endswith(" ")
