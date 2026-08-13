#!/usr/bin/env python3
"""
shipper.py — Tier-2-gated terminal-action cores, extracted from task_server.

Holds the "ship" cores (send-message and Jira-publish, plus their helpers)
that were previously defined inline in task_server.py. Pulling them out here
lets the headless judge / enforcement backend ship without standing up the
HTTP server: it can `import shipper` and call the cores directly. The HTTP
`handle_*` route functions stay in task_server.py, which re-exports these
names so existing route-level monkeypatches keep working.

This module must NOT import task_server (that would create an import cycle).
"""

import json
import os
import re
import sys

# Add script directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import task_lib
import program_lib
import profile_lib
import adapters
import jira_publish
from adapters.project_management._contract import NotConfigured
from adapters.messaging._contract import NotConfigured as MessagingNotConfigured
from adapters import NeedsConfirmation

PM_OS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _message_draft_from_task(task_id):
    """Build the messaging-adapter draft from a send-message task's fields.

    Channel mirrors the frontend's rendering (email if the channel reads
    "email", else Teams). Recipients are resolved name→address via the people
    email cache (literal addresses pass through). Returns the dict the m365
    adapter's publish() consumes."""
    fm = task_lib.read_task(task_id)["frontmatter"] or {}
    raw_channel = (fm.get("message_channel") or "").lower()
    channel = "email" if "email" in raw_channel else "teams"
    cache = _load_email_cache()
    to_display = fm.get("message_to") or ""
    to = []
    for tok in (t.strip() for t in to_display.split(",")):
        if not tok:
            continue
        to.append(tok if "@" in tok else cache.get(tok, tok))
    return {
        "channel": channel,
        "to": to,
        "to_display": to_display,
        "subject": fm.get("message_subject") or "",
        "body": fm.get("message_body") or "",
        "attachments": fm.get("attachments") or [],
        "task_id": task_id,
    }


def _attempt_send_message(task_id, draft):
    """Tier-2 gated send. Returns (status, payload), mirroring _attempt_publish:
      ("needs_confirm", None)      — gate fired; nothing sent
      ("already_sent",  None)      — task already done; nothing sent
      ("unconfigured",  None)      — no messaging provider (caller records manually)
      ("error",  (code, msg))      — NotConfigured -> 400, mgc/RuntimeError -> 502
      ("ok",     (message_id, None))
    On ok, stamps message_sent_at + message_id and archives the task."""
    # Idempotency: a prior send stamps message_sent_at AND marks the task done.
    # Either signal means "already sent" — message_sent_at survives even if the
    # archive step failed, so a retry can't re-send (closes the double-send window).
    try:
        fm = task_lib.read_task(task_id).get("frontmatter") or {}
        if fm.get("status") == "done" or fm.get("message_sent_at"):
            _note(task_id, "Send skipped — task already sent (no duplicate message).")
            return ("already_sent", None)
    except FileNotFoundError:
        pass
    # Fail fast on a recipient we couldn't resolve to a real address/UPN — never
    # send to a bare display name. Only enforced when a provider is actually
    # configured; with no provider the caller records a manual send (labels are fine).
    unresolved = [t for t in (draft.get("to") or []) if "@" not in t]
    if unresolved and adapters.get("messaging") is not None:
        msg = ("Couldn't resolve recipient(s) to an address: " + ", ".join(unresolved)
               + " — add them to datasets/people/email_cache.json or use a full address.")
        _note(task_id, f"Send blocked: {msg}")
        return ("error", (400, msg))
    try:
        result = adapters.publish("messaging", draft)
    except NeedsConfirmation:
        return ("needs_confirm", None)
    except MessagingNotConfigured as e:
        _note(task_id, f"Send failed: {e}")
        return ("error", (400, str(e)))
    except RuntimeError as e:
        # mgc/auth failure — the message carries the actionable `mgc login` hint.
        _note(task_id, f"Send failed: {e}")
        return ("error", (502, f"Send failed: {e}"))
    if result is None:
        return ("unconfigured", None)
    message_id, _url = result
    # Email (sendMail) returns no id — m365 reports "sent"; only stamp a real id.
    changes = {"message_sent_at": task_lib._now_iso(),
               "agent_output": f"Sent via {draft.get('channel')}"}
    if message_id and message_id != "sent":
        changes["message_id"] = message_id
    try:
        task_lib.update_task(task_id, changes=changes,
            comment=f"Message sent via {draft.get('channel')} to {draft.get('to_display')}.",
            actor="system")
        task_lib.complete_task(task_id, actor="system")
    except Exception as e:
        # The send already happened — make the bookkeeping failure VISIBLE (a
        # not-archived-but-sent task could otherwise be retried into a duplicate).
        _note(task_id, f"WARNING: message sent but recording/archive failed: {e}")
    return ("ok", (message_id, None))


def _record_manual_send(task_id):
    """Legacy fallback when no messaging provider is configured: record that the
    operator sent the drafted message themselves, and archive (the pre-mgc
    behavior). Returns the archive path."""
    task_lib.update_task(
        task_id, changes={"message_sent_at": task_lib._now_iso()},
        comment="Message marked as sent (no send provider configured — sent manually from the draft).",
        actor="human")
    return task_lib.complete_task(task_id, actor="human")


def _note(task_id, msg):
    try:
        task_lib.update_task(task_id, changes={}, comment=msg, actor="system")
    except Exception:
        pass


def _attempt_publish(task_id, draft):
    """Shared publish core for the publish-jira and confirm handlers.

    Returns (status, payload):
      ("needs_confirm",      None)        — Tier-2 gate fired; no external call made
      ("already_published",  None)        — task already done; no external call made
      ("unconfigured", (400, msg))        — no provider configured
      ("error",       (code, msg))        — NotConfigured -> 400, RuntimeError -> 500
      ("ok",          (issue_key, url))   — published; task marked done + traced
    Records the full outcome (task comment + LangFuse trace) for the unconfigured,
    error, and ok statuses. The needs_confirm and already_published branches are
    early returns that skip the trace: needs_confirm records nothing (the caller
    emits the confirm card), and already_published records only a single audit
    comment so the duplicate-publish skip is visible in the task log."""
    if draft is None:
        return ("error", (400, "No JIRA_DRAFT block found in task body"))
    # Guard against re-publishing a task that already produced a ticket (double-confirm,
    # retry, separate tab). A JIRA_DRAFT task is marked done only by a successful publish
    # below, so status == "done" means it was already published — never publish twice.
    # Tolerant of a missing/virtual task id (e.g. unit tests that pass a synthetic id).
    session_id = None
    try:
        existing = task_lib.read_task(task_id)
        if (existing.get("frontmatter") or {}).get("status") == "done":
            _note(task_id, "Publish skipped — task already published (no duplicate created).")
            return ("already_published", None)
        session_id = (existing.get("frontmatter") or {}).get("claude_session_id")
    except FileNotFoundError:
        pass
    try:
        result = adapters.publish("project_management", draft, session_id=session_id)
    except NeedsConfirmation:
        return ("needs_confirm", None)
    except NotConfigured as e:
        _note(task_id, f"Jira publish failed: {e}")
        jira_publish._trace_publish(task_id, draft, error=str(e))
        return ("error", (400, str(e)))
    except RuntimeError as e:
        _note(task_id, f"Jira publish failed: {e}")
        jira_publish._trace_publish(task_id, draft, error=str(e))
        return ("error", (500, f"Jira publish failed: {e}"))
    if result is None:
        msg = "No project-management tool is configured for this install"
        _note(task_id, f"Jira publish failed: {msg}")
        jira_publish._trace_publish(task_id, draft, error=msg)
        return ("unconfigured", (400, msg))
    issue_key, issue_url = result
    output_str = f"Created {issue_key}: {issue_url}"
    try:
        task_lib.update_task(task_id, changes={"agent_output": output_str},
                             comment=f"Published to Jira: {output_str}", actor="system")
        task_lib.complete_task(task_id, actor="system")
    except Exception:
        pass
    try:
        _emit_jira_receipt(task_id, issue_key, issue_url, "Created")
    except Exception:
        pass
    jira_publish._trace_publish(task_id, draft, issue_key=issue_key, issue_url=issue_url)
    _maybe_bind_tracker(task_id, issue_key)
    return ("ok", (issue_key, issue_url))


_PROG_RE = re.compile(r"^PROG-\d{4,}$")


def _maybe_bind_tracker(task_id, issue_key):
    """After a Jira publish, bind the issue to the source program if applicable.

    Checks if the source task is tagged with a PROG-XXXX id (bootstrap
    draft-ticket tasks carry [program_id, "cadence"]). If so, writes the
    Jira key as a truth binding on the program. Best-effort: never breaks
    the publish.
    """
    try:
        existing = task_lib.read_task(task_id)
        tags = (existing.get("frontmatter") or {}).get("tags") or []
        program_id = next((t for t in tags if _PROG_RE.match(str(t))), None)
        if not program_id:
            return
        program_lib.set_binding(
            program_id, role="truth", kind="project_management", anchor=issue_key)
    except Exception:
        pass


def _attempt_update(task_id, update):
    """Shared update core for the update-jira handler.

    Returns (status, payload) mirroring _attempt_publish."""
    if update is None:
        return ("error", (400, "No JIRA_UPDATE block found in task body"))
    try:
        existing = task_lib.read_task(task_id)
        if (existing.get("frontmatter") or {}).get("status") == "done":
            _note(task_id, "Update skipped — task already processed.")
            return ("already_updated", None)
    except FileNotFoundError:
        pass

    action = update.get("action", "comment")
    expected = update.get("expected_status", "")
    if action in ("transition", "transition_and_comment") and expected:
        try:
            current = jira_publish.fetch_issue(update["issue_key"])
            if current and current.get("status", "").lower() != expected.lower():
                msg = (f"Drift resolved: {update['issue_key']} is now "
                       f"'{current['status']}' (was '{expected}' when observed). "
                       f"Transition skipped.")
                _note(task_id, msg)
                try:
                    task_lib.complete_task(task_id, actor="system")
                except Exception:
                    pass
                return ("drift_resolved", msg)
        except Exception:
            pass

    try:
        result = adapters.update_issue("project_management", update)
    except NeedsConfirmation:
        return ("needs_confirm", None)
    except NotConfigured as e:
        _note(task_id, f"Jira update failed: {e}")
        jira_publish._trace_update(task_id, update, error=str(e))
        return ("error", (400, str(e)))
    except RuntimeError as e:
        _note(task_id, f"Jira update failed: {e}")
        jira_publish._trace_update(task_id, update, error=str(e))
        return ("error", (500, f"Jira update failed: {e}"))
    if result is None:
        msg = "No project-management tool is configured for this install"
        _note(task_id, f"Jira update failed: {msg}")
        jira_publish._trace_update(task_id, update, error=msg)
        return ("unconfigured", (400, msg))
    issue_key, issue_url = result
    action = update.get("action", "comment")
    action_label = {"comment": "Commented on", "edit": "Updated", "comment_and_edit": "Updated and commented on"}.get(action, "Updated")
    output_str = f"{action_label} {issue_key}: {issue_url}"
    try:
        task_lib.update_task(task_id, changes={"agent_output": output_str},
                             comment=f"Jira update: {output_str}", actor="system")
        task_lib.complete_task(task_id, actor="system")
    except Exception:
        pass
    try:
        _emit_jira_receipt(task_id, issue_key, issue_url, action_label)
    except Exception:
        pass
    jira_publish._trace_update(task_id, update, issue_key=issue_key, issue_url=issue_url)
    return ("ok", (issue_key, issue_url))


def _emit_jira_receipt(source_task_id, issue_key, issue_url, action_label):
    """Emit a lightweight receipt card with the Jira link so the operator can
    verify the result without searching Jira manually."""
    src = task_lib.read_task(source_task_id)
    src_title = (src.get("frontmatter") or {}).get("title", "")
    cid, _ = task_lib.create_task(
        f"{action_label} {issue_key}",
        queue="collab", domain="ops", creator="agent",
        description=(f"**[{issue_key}]({issue_url})**\n\n"
                     f"Source: {src_title}\n\n"
                     f"Open the link above to verify in Jira."),
        card_type="receipt")
    task_lib.update_task(cid, changes={
        "receipt_kind": "jira",
        "receipt_summary": f"{action_label} {issue_key}",
        "issue_key": issue_key,
        "issue_url": issue_url,
    })
    task_lib.complete_task(cid, actor="system")
    return cid


def _emit_confirm_card(family, source_task):
    """Write a Tier-2 confirm card to the collab queue. Confirm flips consent and
    re-drives source_task; Reject holds off. Carries the link fields handle_confirm reads."""
    provider = (profile_lib.provider(family) or "").title() or "your tool"
    summary = f"Okay to let this assistant post to your {provider}?"
    cid, _ = task_lib.create_task(
        summary, queue="collab", domain="ops", creator="agent",
        description=(f"This is the first time it will write to your {provider}. "
                     "Confirm to allow it from now on, or Reject to hold off."),
        card_type="confirm")
    task_lib.update_task(cid, changes={
        "confirm_family": family,
        "confirm_source_task": source_task,
        "receipt_summary": summary,   # shown on the task detail view; the card-list face falls back to the title
    })
    return cid


def autoship(task_id, action_type):
    """Auto-ship an autonomous action-type's terminal action (Tier-2 gated).
    Returns 'shipped' | 'needs_confirm' | 'error'. On needs_confirm, emits the
    one-time Tier-2 confirm card (autonomy never bypasses the first-write confirm)
    and leaves the task parked. On success, emits a Keep/Undo receipt."""
    if action_type == "send-message":
        draft = _message_draft_from_task(task_id)
        status, payload = _attempt_send_message(task_id, draft)
        family = "messaging"
        what = f"Sent {draft.get('channel') or 'message'} to {draft.get('to_display') or 'recipient'}"
    elif action_type == "publish-ticket":
        import jira_publish
        body = task_lib.read_task(task_id).get("body", "")
        draft = jira_publish.parse_jira_draft(body)
        status, payload = _attempt_publish(task_id, draft)
        family = "project_management"
        what = f"Published {payload[0]}" if status == "ok" and payload else "Published ticket"
    else:
        return "error"
    if status == "needs_confirm":
        _emit_confirm_card(family, task_id)
        return "needs_confirm"
    if status == "ok":
        _emit_autoship_receipt(task_id, action_type, what)
        return "shipped"
    if status in ("already_sent", "already_published"):
        return "shipped"
    return "error"


def _emit_autoship_receipt(source_task_id, action_type, what):
    """A never-deleted Keep/Undo receipt for an auto-shipped action. Undo demotes
    the type to supervised (it cannot un-send — the receipt copy says so)."""
    src = task_lib.read_task(source_task_id).get("frontmatter") or {}
    cid, _ = task_lib.create_task(
        f"Auto-shipped: {what}", queue="collab", domain="ops", creator="agent",
        description=(f"Autonomous Mode shipped this without a per-instance approve.\n\n"
                     f"**{what}**\n\nThe external action already happened. **Keep** to "
                     f"acknowledge, or **Undo** to stop auto-shipping '{action_type}' "
                     f"(drops it back to supervised — it cannot un-send)."),
        card_type="receipt")
    task_lib.update_task(cid, changes={
        "receipt_kind": "autoship",
        "autoship_task_type": action_type,
        "receipt_summary": what,
        "judge_score": src.get("judge_score"),
        "judge_why": src.get("judge_why"),
    })
    return cid


def _load_email_cache():
    """Load the people email cache (name → email mapping)."""
    email_cache_path = os.path.join(PM_OS_DIR, "datasets", "people", "email_cache.json")
    try:
        with open(email_cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
