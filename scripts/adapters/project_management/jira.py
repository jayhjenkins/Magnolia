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

    A read of the team's tracker is NOT an external write, so it is never Tier-2
    gated. But the Jira read path is not wired here: the existing jira_publish
    helpers only WRITE issues. Rather than fabricate facts, this raises
    NotConfigured with a clear message - an honest seam that degrades gracefully
    (adapters.fetch_status turns the unconfigured signal into a clean None).
    """
    if not is_configured(root):
        raise NotConfigured("Jira is not configured in this profile")
    raise NotConfigured(
        "Jira read op (fetch_status) is not wired - the jira_publish helpers only "
        "write issues. Wire a read against the Jira MCP to ground tracker-truth.")
