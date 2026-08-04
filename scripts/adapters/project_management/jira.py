"""Jira project-management adapter. Wraps the existing jira_publish helpers."""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import profile_lib  # noqa: E402
import jira_publish  # noqa: E402
from adapters.project_management._contract import NotConfigured  # noqa: E402


def is_configured(root=None) -> bool:
    cfg = profile_lib.jira_config(root)
    return bool(cfg.get("cloud_id") and cfg.get("project_key"))


def _has_api_creds(root=None) -> bool:
    """True when the profile has api_email and api_token for REST reads."""
    cfg = profile_lib.jira_config(root)
    return bool(cfg.get("api_email") and cfg.get("api_token"))


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

    Calls the Jira REST API v3 directly (not via claude -p) using
    api_email + api_token from the profile. A read is free -- never
    Tier-2 gated.
    """
    if not is_configured(root):
        raise NotConfigured("Jira is not configured in this profile")
    if not _has_api_creds(root):
        raise NotConfigured(
            "Jira read requires api_email and api_token in profile "
            "(project_management.jira)")

    cfg = profile_lib.jira_config(root)
    cloud_id = cfg["cloud_id"]
    api_email = cfg["api_email"]
    api_token = cfg["api_token"]

    if cloud_id.startswith("http"):
        base_url = cloud_id.rstrip("/")
    elif "." in cloud_id:
        base_url = f"https://{cloud_id}"
    else:
        base_url = f"https://api.atlassian.com/ex/jira/{cloud_id}"

    url = f"{base_url}/rest/api/3/issue/{issue_key}?fields=summary,status,duedate"
    creds = base64.b64encode(f"{api_email}:{api_token}".encode()).decode()

    req = urllib.request.Request(url, headers={
        "Authorization": f"Basic {creds}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"Jira REST API error {e.code}: {e.reason}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"Jira REST API unreachable: {e}")

    fields = data.get("fields") or {}
    status_obj = fields.get("status") or {}
    return {
        "status": status_obj.get("name") or "",
        "title": fields.get("summary") or "",
        "due": fields.get("duedate"),
    }
