"""Jira project-management adapter. Wraps the existing jira_publish helpers."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import profile_lib  # noqa: E402
import jira_publish  # noqa: E402
from adapters.project_management._contract import NotConfigured  # noqa: E402


def is_configured(root=None) -> bool:
    cfg = profile_lib.jira_config(root)
    return bool(cfg.get("cloud_id") and cfg.get("project_key"))


def publish(draft, root=None, session_id=None):
    if not is_configured(root):
        raise NotConfigured("Jira is not configured in this profile")
    return jira_publish.publish_to_jira(draft, session_id=session_id)


def update(update_dict, root=None):
    if not is_configured(root):
        raise NotConfigured("Jira is not configured in this profile")
    return jira_publish.execute_jira_update(update_dict)


def comment(update_dict, root=None):
    if not is_configured(root):
        raise NotConfigured("Jira is not configured in this profile")
    update_dict = dict(update_dict, action="comment")
    return jira_publish.execute_jira_update(update_dict)


def fetch_status(issue_key, root=None):
    """READ op: return {"status","title","due"} for an issue, or None if absent.

    Calls getJiraIssue via the same MCP pattern as the write path. A read
    is free -- never Tier-2 gated.
    """
    if not is_configured(root):
        raise NotConfigured("Jira is not configured in this profile")
    return jira_publish.fetch_issue(issue_key)
