#!/usr/bin/env python3
"""
jira_publish.py — Parse a JIRA_DRAFT block from a task and publish to Jira
via a mini Claude session using the Jira MCP connector.

Called by the task server when the user clicks "Publish to Jira" in the UI.
"""

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import platform_lib
import profile_lib

PM_OS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Jira config — sourced from the active profile via profile_lib (not hardcoded).
_jira = profile_lib.jira_config()
JIRA_CLOUD_ID = _jira.get("cloud_id", "")
JIRA_PROJECT_KEY = _jira.get("project_key", "")
JIRA_COMPONENT_ID = _jira.get("component_id", "")
JIRA_AUTO_LABEL = _jira.get("auto_label", "")
JIRA_DEFAULT_ASSIGNEE = _jira.get("default_assignee", "")

# Base for issue browse URLs, derived from the profile's cloud_id (e.g.
# "https://yourorg.atlassian.net/browse"). Empty until a profile is configured.
JIRA_BROWSE_BASE = f"https://{JIRA_CLOUD_ID}/browse" if JIRA_CLOUD_ID else ""

# Canonical type names. Drafts that arrive with different casing or shorthand
# get normalized so Jira's case-sensitive issueTypeName check doesn't fail.
JIRA_TYPE_CANONICAL = {
    "bug": "Bug",
    "regression defect": "Regression Defect",
    "regression": "Regression Defect",
    "story": "Story",
    "unit": "Unit",
    "epic": "Epic",
    "feature": "Feature",
    "spike": "Spike",
    "hotfix": "Hotfix",
    "work item defect": "Work Item Defect",
    "performance defect": "Performance Defect",
    "security defect": "Security Defect",
}

# Types that use the Feature/Epic-name custom field (customfield_10011).
NAMED_PARENT_TYPES = {"Feature", "Epic"}


def normalize_type(raw):
    """Map common casings/shorthand to the canonical Jira issue type name."""
    if not raw:
        return "Bug"
    return JIRA_TYPE_CANONICAL.get(raw.strip().lower(), raw.strip())


# ─── Draft Parsing ───────────────────────────────────────────────────────────

def parse_jira_draft(body):
    """Extract JIRA_DRAFT fields from a task body string.

    Returns dict with: type, summary, description, priority, labels,
    release_notes, feature_name, gtm_date, client_commitment, parent.
    Returns None if no draft found.
    """
    if not body or "<!-- JIRA_DRAFT -->" not in body:
        return None

    # Extract the draft block
    match = re.search(r"<!-- JIRA_DRAFT -->(.+?)<!-- /JIRA_DRAFT -->", body, re.DOTALL)
    if not match:
        return None

    block = match.group(1)

    def _field(name):
        m = re.search(rf"<!-- {name}:(.+?) -->", block)
        return m.group(1).strip() if m else ""

    # JIRA_FEATURE_NAME is preferred; JIRA_EPIC_NAME accepted as legacy fallback.
    feature_name = _field("JIRA_FEATURE_NAME") or _field("JIRA_EPIC_NAME") or ""

    def _date_or_empty(name):
        # TBD and empty both mean "leave the Jira date field blank"
        raw = _field(name)
        return "" if raw.lower() == "tbd" else raw

    # Extract structured fields from HTML comments
    draft = {
        "type": normalize_type(_field("JIRA_TYPE") or "Bug"),
        "summary": _field("JIRA_SUMMARY") or "",
        "priority": _field("JIRA_PRIORITY") or "",
        "labels": [l.strip() for l in _field("JIRA_LABELS").split(",") if l.strip()],
        "release_notes": _field("JIRA_RELEASE_NOTES") or "",
        "feature_name": feature_name,
        # Kept for backwards-compatible callers; mirrors feature_name.
        "epic_name": feature_name,
        "gtm_date": _date_or_empty("JIRA_GTM_DATE"),
        "ea_date": _date_or_empty("JIRA_EA_DATE"),
        "spec_reference": _field("JIRA_SPEC_REFERENCE") or "",
        "client_commitment": _field("JIRA_CLIENT_COMMITMENT") or "",
        "parent": _field("JIRA_PARENT") or "",
        "assignee": _field("JIRA_ASSIGNEE") or "",
    }

    # Extract the description from the ### Description section
    desc_match = re.search(r"### Description\s*\n(.*?)(?=\n### |\n<!-- /JIRA_DRAFT)", block, re.DOTALL)
    if desc_match:
        draft["description"] = desc_match.group(1).strip()
    else:
        # Fallback: use everything between ### Summary and ### Fields
        fallback = re.search(r"### Summary\s*\n.*?\n(.*?)(?=\n### Fields|\n<!-- /JIRA_DRAFT)", block, re.DOTALL)
        draft["description"] = fallback.group(1).strip() if fallback else ""

    if not draft["summary"]:
        return None

    return draft


# ─── Claude Prompt Building ──────────────────────────────────────────────────

def build_claude_prompt(draft):
    """Build a constrained prompt for Claude to call the Jira MCP tool."""
    issue_type = normalize_type(draft.get("type") or "Bug")

    # Labels come straight from the draft. The skill's Swim Lane Rule decides
    # whether home_aidlc is present (Features/Epics yes; Bugs/Units/etc. no).
    # Dedupe only — preserve the draft's order and intent.
    seen = set()
    labels = []
    for l in (draft.get("labels") or []):
        if l and l not in seen:
            seen.add(l)
            labels.append(l)

    # Build additional_fields
    additional_fields = {
        "components": [{"id": JIRA_COMPONENT_ID}],
        "labels": labels,
    }

    if draft.get("priority"):
        additional_fields["priority"] = {"name": draft["priority"]}

    if draft.get("release_notes"):
        additional_fields["customfield_10499"] = {"value": draft["release_notes"]}

    # Feature / Epic — use customfield_10011 for the short name.
    if issue_type in NAMED_PARENT_TYPES:
        name = draft.get("feature_name") or draft.get("epic_name")
        if name:
            additional_fields["customfield_10011"] = name
        if draft.get("gtm_date"):
            additional_fields["customfield_10300"] = draft["gtm_date"]
        if draft.get("ea_date"):
            additional_fields["customfield_10683"] = draft["ea_date"]
        if draft.get("spec_reference"):
            additional_fields["customfield_10783"] = draft["spec_reference"]
        if draft.get("client_commitment"):
            additional_fields["customfield_10298"] = [draft["client_commitment"]]

    # Parent link — typically for Unit → Feature/Epic. Jira accepts a top-level
    # `parent` key in additional_fields.
    parent_key = (draft.get("parent") or "").strip()
    if parent_key:
        additional_fields["parent"] = {"key": parent_key}

    # Assignee — named-parent types default to the profile assignee unless the draft overrides.
    assignee_id = (draft.get("assignee") or "").strip()
    if issue_type in NAMED_PARENT_TYPES:
        additional_fields["assignee"] = {"accountId": assignee_id or JIRA_DEFAULT_ASSIGNEE}
    elif assignee_id:
        additional_fields["assignee"] = {"accountId": assignee_id}

    additional_fields_json = json.dumps(additional_fields)

    # Escape for shell
    summary_escaped = draft["summary"].replace('"', '\\"')
    description_escaped = draft["description"].replace('"', '\\"')

    prompt = f"""Create this Jira issue:

Tool: mcp__claude_ai_Jira__createJiraIssue
Parameters:
  cloudId: "{JIRA_CLOUD_ID}"
  projectKey: "{JIRA_PROJECT_KEY}"
  issueTypeName: "{issue_type}"
  summary: "{summary_escaped}"
  description: "{description_escaped}"
  contentFormat: "markdown"
  additional_fields: {additional_fields_json}

Report the result as: JIRA_RESULT:ISSUE_KEY|ISSUE_URL
Example: JIRA_RESULT:{JIRA_PROJECT_KEY}-1234|{JIRA_BROWSE_BASE}/{JIRA_PROJECT_KEY}-1234
On failure: JIRA_ERROR:reason"""

    return prompt


# ─── Update Parsing ─────────────────────────────────────────────────────────

def parse_jira_update(body):
    """Extract JIRA_UPDATE fields from a task body string.

    Returns dict with: issue_key, action, summary, priority, labels,
    comment_body, description.
    Returns None if no update block found or issue_key is missing.
    """
    if not body or "<!-- JIRA_UPDATE -->" not in body:
        return None

    match = re.search(r"<!-- JIRA_UPDATE -->(.+?)<!-- /JIRA_UPDATE -->", body, re.DOTALL)
    if not match:
        return None

    block = match.group(1)

    def _field(name):
        m = re.search(rf"<!-- {name}:(.+?) -->", block)
        return m.group(1).strip() if m else ""

    issue_key = _field("JIRA_ISSUE_KEY")
    if not issue_key:
        return None

    action = _field("JIRA_ACTION") or "comment"

    labels_raw = _field("JIRA_LABELS")
    labels = [l.strip() for l in labels_raw.split(",") if l.strip()] if labels_raw else []

    # Extract ### Comment section
    comment_match = re.search(r"### Comment\s*\n(.*?)(?=\n### |\Z)", block, re.DOTALL)
    comment_body = comment_match.group(1).strip() if comment_match else ""

    # Extract ### Description section
    desc_match = re.search(r"### Description\s*\n(.*?)(?=\n### |\Z)", block, re.DOTALL)
    description = desc_match.group(1).strip() if desc_match else ""

    return {
        "issue_key": issue_key,
        "action": action,
        "summary": _field("JIRA_SUMMARY") or "",
        "priority": _field("JIRA_PRIORITY") or "",
        "labels": labels,
        "comment_body": comment_body,
        "description": description,
    }


def build_comment_prompt(update):
    """Build a constrained prompt for Claude to call addCommentToJiraIssue."""
    issue_key = update["issue_key"]
    comment_body = update["comment_body"].replace('"', '\\"')

    prompt = f"""Add a comment to Jira issue {issue_key}:

Tool: mcp__claude_ai_Jira__addCommentToJiraIssue
Parameters:
  cloudId: "{JIRA_CLOUD_ID}"
  issueIdOrKey: "{issue_key}"
  commentBody: "{comment_body}"
  contentFormat: "markdown"

Report the result as: JIRA_RESULT:{issue_key}|{JIRA_BROWSE_BASE}/{issue_key}
On failure: JIRA_ERROR:reason"""

    return prompt


def build_edit_prompt(update):
    """Build a constrained prompt for Claude to call editJiraIssue."""
    issue_key = update["issue_key"]

    fields = {}
    if update.get("summary"):
        fields["summary"] = update["summary"]
    if update.get("priority"):
        fields["priority"] = {"name": update["priority"]}
    if update.get("labels"):
        fields["labels"] = update["labels"]
    if update.get("description"):
        fields["description"] = update["description"]

    fields_json = json.dumps(fields)

    prompt = f"""Update Jira issue {issue_key}:

Tool: mcp__claude_ai_Jira__editJiraIssue
Parameters:
  cloudId: "{JIRA_CLOUD_ID}"
  issueIdOrKey: "{issue_key}"
  fields: {fields_json}
  contentFormat: "markdown"

Report the result as: JIRA_RESULT:{issue_key}|{JIRA_BROWSE_BASE}/{issue_key}
On failure: JIRA_ERROR:reason"""

    return prompt


# ─── Publishing ──────────────────────────────────────────────────────────────

JIRA_SYSTEM_PROMPT = (
    "This is an automated pipeline step. Tier-2 confirmation has already "
    "been obtained from the human operator. Proceed directly with the "
    "requested tool call. Do not ask for confirmation or present previews."
)


def _run_jira_session(prompt, allowed_tools, session_id=None):
    """Spawn a mini Claude session to execute a Jira MCP call.

    Takes the full prompt and a comma-separated string of allowed tool names.
    If session_id is provided, resumes that session so the model has full
    context from the ticket-creation conversation (avoids prompt-injection
    refusals on fresh sessions).
    Returns (issue_key, issue_url) on success.
    Raises RuntimeError on failure.
    """
    env = platform_lib.headless_claude_env()
    claude_bin = platform_lib.resolve_claude()

    cmd = [claude_bin, "-p", prompt, "--max-turns", "3",
           "--allowedTools", allowed_tools]
    if session_id:
        cmd.extend(["--resume", session_id])
    else:
        cmd.extend(["--append-system-prompt", JIRA_SYSTEM_PROMPT])

    try:
        result = subprocess.run(
            cmd,
            cwd=PM_OS_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude session timed out after 120 seconds")

    output = result.stdout + "\n" + result.stderr

    # Parse result
    match = re.search(r"JIRA_RESULT:([^|\s]+)\|(\S+)", output)
    if match:
        return match.group(1), match.group(2)

    # Check for error
    err_match = re.search(r"JIRA_ERROR:(.+)", output)
    if err_match:
        raise RuntimeError(f"Jira operation failed: {err_match.group(1).strip()}")

    # Without a configured project key the fallback pattern degrades to
    # ``-\d+``, which false-positive matches dates/error codes (e.g. "403-1").
    # Treat the unconfigured-profile case as unparseable rather than guessing.
    if not JIRA_PROJECT_KEY:
        raise RuntimeError(f"Could not parse Jira result from Claude output. Exit code: {result.returncode}. Output: {output[:500]}")

    # Try to find issue key in output (fallback) — pattern is built from the
    # active profile's project key and cloud_id, not a hardcoded tenant.
    key_pat = re.escape(JIRA_PROJECT_KEY) + r"-\d+"
    key_match = re.search(rf"({key_pat})", output)
    url_match = re.search(rf"({re.escape(JIRA_BROWSE_BASE)}/{key_pat})", output) if JIRA_BROWSE_BASE else None
    if key_match:
        url = url_match.group(1) if url_match else f"{JIRA_BROWSE_BASE}/{key_match.group(1)}"
        return key_match.group(1), url

    raise RuntimeError(f"Could not parse Jira result from Claude output. Exit code: {result.returncode}. Output: {output[:500]}")


def publish_to_jira(draft, session_id=None):
    """Spawn a mini Claude session to publish the draft to Jira.

    If session_id is provided, resumes the task's original Claude session
    so the model has full context and doesn't treat the request as injection.
    Returns (issue_key, issue_url) on success.
    Raises RuntimeError on failure.
    """
    prompt = build_claude_prompt(draft)
    return _run_jira_session(prompt, "mcp__claude_ai_Jira__createJiraIssue",
                             session_id=session_id)


def _run_jira_read_session(issue_key):
    """Spawn a mini Claude session to read a Jira issue via MCP.

    Returns the raw output string for parsing by fetch_issue.
    Raises RuntimeError on failure.
    """
    prompt = f"""Read Jira issue {issue_key}:

Tool: mcp__claude_ai_Jira__getJiraIssue
Parameters:
  cloudId: "{JIRA_CLOUD_ID}"
  issueIdOrKey: "{issue_key}"
  fields: ["summary", "status", "duedate"]

Report the result as a single line:
JIRA_READ:status_name|summary_text|due_date_or_none

For example: JIRA_READ:In Progress|Build the feed widget|2026-09-15
Or if no due date: JIRA_READ:Done|Ship the feature|none
If the issue is not found: JIRA_READ:NOT_FOUND"""

    env = platform_lib.headless_claude_env()
    claude_bin = platform_lib.resolve_claude()

    try:
        result = subprocess.run(
            [claude_bin, "-p", prompt, "--max-turns", "3",
             "--allowedTools", "mcp__claude_ai_Jira__getJiraIssue"],
            cwd=PM_OS_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude session timed out after 120 seconds")

    return result.stdout + "\n" + result.stderr


def fetch_issue(issue_key):
    """Read a Jira issue via MCP and return {"status", "title", "due"} or None.

    Uses the same MCP pattern as the write path. This is a READ op
    (no Tier-2 gate). Returns None if the issue is not found.
    Raises RuntimeError on failure.
    """
    output = _run_jira_read_session(issue_key)

    match = re.search(r"JIRA_READ:(.+)", output)
    if not match:
        raise RuntimeError(
            f"Could not parse Jira read from Claude output. Output: {output[:500]}")

    payload = match.group(1).strip()
    if payload == "NOT_FOUND":
        return None

    parts = payload.split("|", 2)
    if len(parts) < 2:
        raise RuntimeError(f"Malformed JIRA_READ payload: {payload}")

    status = parts[0].strip()
    title = parts[1].strip()
    due = parts[2].strip() if len(parts) > 2 else None
    if due and due.lower() == "none":
        due = None

    return {"status": status, "title": title, "due": due}


# ─── Updating ──────────────────────────────────────────────────────────────

def execute_jira_update(update):
    """Execute a Jira update based on the parsed update action.

    Based on update["action"]:
    - "comment"          -> add a comment
    - "edit"             -> edit issue fields
    - "comment_and_edit" -> edit first, then comment

    Returns (issue_key, issue_url) on success.
    Raises RuntimeError on failure.
    """
    action = update.get("action", "comment")

    if action == "comment":
        return _run_jira_session(
            build_comment_prompt(update),
            "mcp__claude_ai_Jira__addCommentToJiraIssue",
        )
    elif action == "edit":
        return _run_jira_session(
            build_edit_prompt(update),
            "mcp__claude_ai_Jira__editJiraIssue",
        )
    elif action == "comment_and_edit":
        # Edit first, then comment. Return the result from the last successful call.
        _run_jira_session(
            build_edit_prompt(update),
            "mcp__claude_ai_Jira__editJiraIssue",
        )
        return _run_jira_session(
            build_comment_prompt(update),
            "mcp__claude_ai_Jira__addCommentToJiraIssue",
        )
    else:
        raise RuntimeError(f"Unknown JIRA_UPDATE action: {action}")


# ─── LangFuse Tracing ───────────────────────────────────────────────────────

def _trace_publish(task_id, draft, issue_key=None, issue_url=None, error=None):
    """Create a LangFuse trace for the publish operation."""
    try:
        from langfuse_client import create_trace
        create_trace(
            name="jira-publish",
            session_id=task_id,
            metadata={
                "jira_type": draft.get("type"),
                "jira_summary": draft.get("summary"),
            },
            tags=["jira", "publish"],
            input_data={
                "type": draft.get("type"),
                "summary": draft.get("summary"),
                "priority": draft.get("priority"),
                "labels": draft.get("labels"),
            },
            output_data={
                "issue_key": issue_key,
                "issue_url": issue_url,
                "error": error,
            },
        )
    except Exception:
        pass


def _trace_update(task_id, update, issue_key=None, issue_url=None, error=None):
    """Create a LangFuse trace for the update operation."""
    try:
        from langfuse_client import create_trace
        create_trace(
            name="jira-update",
            session_id=task_id,
            metadata={
                "jira_issue_key": update.get("issue_key"),
                "jira_action": update.get("action"),
            },
            tags=["jira", "update"],
            input_data={
                "issue_key": update.get("issue_key"),
                "action": update.get("action"),
                "summary": update.get("summary"),
                "priority": update.get("priority"),
                "labels": update.get("labels"),
            },
            output_data={
                "issue_key": issue_key,
                "issue_url": issue_url,
                "error": error,
            },
        )
    except Exception:
        pass


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Publish a Jira draft from a PM-OS task")
    parser.add_argument("--task", required=True, help="Task ID (e.g., TASK-0123)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and display draft without publishing")
    args = parser.parse_args()

    import task_lib
    task_data = task_lib.read_task(args.task)
    body = task_data.get("body", "")

    draft = parse_jira_draft(body)
    if draft is None:
        print("Error: No JIRA_DRAFT block found in task body", file=sys.stderr)
        sys.exit(1)

    # Labels submitted as-is (deduped). No auto-prepend — Swim Lane Rule lives
    # in the draft, not here.
    effective_labels = []
    for l in draft["labels"]:
        if l and l not in effective_labels:
            effective_labels.append(l)

    if not effective_labels:
        lane_hint = " ('everything else' column)"
    elif JIRA_AUTO_LABEL in effective_labels:
        lane_hint = " (AI DLC swim lane)"
    else:
        lane_hint = ""

    print(f"Parsed Jira Draft:")
    print(f"  Type:        {draft['type']}")
    print(f"  Summary:     {draft['summary']}")
    print(f"  Priority:    {draft['priority'] or '(default)'}")
    print(f"  Labels:      {', '.join(effective_labels) or '(none)'}{lane_hint}")
    print(f"  Release:     {draft['release_notes'] or '(none)'}")
    if draft.get("parent"):
        print(f"  Parent:      {draft['parent']}")
    if draft["type"] in NAMED_PARENT_TYPES:
        print(f"  {draft['type']} Name: {draft.get('feature_name') or draft.get('epic_name') or '(none)'}")
        print(f"  GTM Date:    {draft['gtm_date'] or '(none)'}")
        print(f"  EA Date:     {draft['ea_date'] or '(none)'}")
        print(f"  Spec Ref:    {draft['spec_reference'] or '(none)'}")
        print(f"  Commitment:  {draft['client_commitment'] or '(none)'}")
        assignee_display = draft.get("assignee") or JIRA_DEFAULT_ASSIGNEE + f" (default: {profile_lib.display_name()})"
        print(f"  Assignee:    {assignee_display}")
    print(f"  Description: {draft['description'][:200]}...")

    if args.dry_run:
        print("\n[DRY RUN] Would publish with prompt:")
        print(build_claude_prompt(draft)[:500] + "...")
        return

    print("\nPublishing to Jira...")
    try:
        key, url = publish_to_jira(draft)
        print(f"\nSuccess! Created {key}: {url}")
        _trace_publish(args.task, draft, issue_key=key, issue_url=url)
    except RuntimeError as e:
        print(f"\nError: {e}", file=sys.stderr)
        _trace_publish(args.task, draft, error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
