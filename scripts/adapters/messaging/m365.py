"""Microsoft 365 messaging adapter — Outlook email + Teams chat via mgc (Graph).

publish(draft) dispatches on draft["channel"]:
  - "email" -> Graph sendMail
  - "teams" -> create/reuse the chat + post the message

Both shell out through send_message_graph (the mgc seam). is_configured is just
"mgc present"; live auth is verified at send time by the mgc call itself
(send_message_graph surfaces the unified-scope login hint on an auth failure).
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import send_message_graph as graph  # noqa: E402
import doc_sync  # noqa: E402 — the md->docx + SharePoint-URL seam (reused, not rebuilt)
from adapters.messaging._contract import NotConfigured  # noqa: E402

# Cached signed-in UPN (needed to build the Teams chat member list). One resolve
# per process; messaging is low-volume so a module-level cache is plenty.
_ME_UPN = None


def is_configured(root=None) -> bool:
    return bool(shutil.which("mgc"))


def _resolve_me_upn():
    """The signed-in user's UPN, via `mgc me get` (cached)."""
    global _ME_UPN
    if _ME_UPN:
        return _ME_UPN
    # `users get` also requires --user-id ("me" = signed-in user).
    out = graph._run_mgc(["users", "get", "--user-id", "me", "--select", "userPrincipalName"]) or {}
    upn = out.get("userPrincipalName")
    if not upn:
        raise NotConfigured("could not resolve the signed-in user (mgc me get)")
    _ME_UPN = upn
    return upn


def _inline_link_block(paths):
    """An ASCII-safe link block appended to a body when an attachment can't be
    attached (the graceful-degradation fallback). Never drops the artifact."""
    return "\n\nAttachments (link):\n" + "\n".join(f"- {p}" for p in paths)


def _resolve_attachments(paths, channel, root=None):
    """Prepare attachments for the channel, degrading any that can't be prepared.

    Returns (send_attachments, degraded_paths, tmp_files):
      - email: send_attachments is a list of local file paths (a markdown source
        is rendered to a temp .docx via doc_sync; pandoc-missing -> degrade).
      - teams: send_attachments is a list of {"name","url"} reference dicts (md ->
        docx in OneDrive -> SharePoint URL; no URL resolvable -> degrade).
    A path that can't be prepared lands in degraded_paths (inline link instead).
    NEVER raises: an attachment failure must not block the send (invariant: the
    artifact is always delivered, as an attachment or a link, never silently lost)."""
    send_atts, degraded, tmp_files = [], [], []
    for p in paths:
        try:
            if channel == "email":
                src = p
                if p.lower().endswith(".md"):
                    fd, tmp = tempfile.mkstemp(suffix=".docx")
                    os.close(fd)
                    doc_sync.md_to_docx(p, tmp)  # raises RuntimeError if pandoc missing
                    tmp_files.append(tmp)
                    src = tmp
                if not os.path.exists(src):
                    raise FileNotFoundError(src)
                send_atts.append(src)
            else:  # teams — reference a hosted file by URL (no base64 path exists)
                url = None
                if p.lower().endswith(".md"):
                    doc_sync.sync_one(p)  # land the docx in OneDrive (raises if unconfigured)
                    url = doc_sync.sharepoint_url_for(p)
                if not url:
                    raise RuntimeError("no hosted URL for attachment")
                send_atts.append({"name": os.path.basename(p), "url": url})
        except (Exception, SystemExit):
            # SystemExit too (not just Exception): doc_sync.load_config() calls
            # sys.exit(1) when doc_sync is unconfigured (the default on a fresh
            # install / no sync_config.yaml), which is a BaseException and would
            # otherwise escape and CRASH the send. The degrade-to-inline-link
            # guarantee must hold regardless of doc_sync config state.
            degraded.append(p)
    return send_atts, degraded, tmp_files


def publish(draft, root=None):
    """Send `draft` and return (message_id, url|None). Raises NotConfigured when
    mgc is unavailable or the channel is unknown; send_message_graph raises
    RuntimeError on an mgc/auth failure (caller surfaces the login hint).

    `draft["attachments"]` (optional) is a list of local artifact paths; they are
    attached (email base64 / Teams reference) and degrade to inline body links."""
    if not is_configured(root):
        raise NotConfigured("Microsoft Graph CLI (mgc) is not available")
    channel = (draft.get("channel") or "").lower()
    if channel not in ("email", "teams"):
        raise NotConfigured(f"unknown messaging channel: {channel!r}")
    to = draft.get("to") or []
    body = draft.get("body") or ""
    send_atts, tmp_files = [], []
    raw_atts = draft.get("attachments") or []
    if raw_atts:
        send_atts, degraded, tmp_files = _resolve_attachments(raw_atts, channel, root)
        if degraded:
            body = body + _inline_link_block(degraded)
    try:
        if channel == "email":
            res = graph.send_email(to, draft.get("subject", ""), body,
                                   attachments=send_atts or None)
            return (res.get("status", "sent"), None)
        me = _resolve_me_upn()
        res = graph.send_teams(me, to, body, attachments=send_atts or None)
        return (res.get("message_id") or res.get("status", "sent"), None)
    finally:
        for t in tmp_files:
            try:
                os.unlink(t)
            except OSError:
                pass
