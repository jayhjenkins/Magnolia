"""Task 1 — the mgc Graph seam for sending email + Teams messages.

Pure payload builders are asserted exactly; the impure mgc runner is exercised
only via dry-run and the missing-mgc guard. No real `mgc` is ever invoked.
Mirrors the calendar precedent (create_calendar_event.py).
"""
import base64
import json
import send_message_graph as g


# ── Pure payload builders ────────────────────────────────────────────────────

def test_email_payload_is_graph_sendmail_shape():
    p = g.build_email_payload(["a@x.com", "b@y.com"], "Subject here", "Hello body")
    assert p["saveToSentItems"] is True
    msg = p["message"]
    assert msg["subject"] == "Subject here"
    assert msg["body"] == {"contentType": "Text", "content": "Hello body"}
    assert msg["toRecipients"] == [
        {"emailAddress": {"address": "a@x.com"}},
        {"emailAddress": {"address": "b@y.com"}},
    ]


def test_email_payload_html_flag():
    p = g.build_email_payload(["a@x.com"], "S", "<b>hi</b>", html=True)
    assert p["message"]["body"]["contentType"] == "HTML"


def test_chat_create_payload_one_on_one():
    p = g.build_chat_create_payload("me@co.com", ["them@co.com"])
    assert p["chatType"] == "oneOnOne"
    binds = [m["user@odata.bind"] for m in p["members"]]
    assert "https://graph.microsoft.com/v1.0/users('me@co.com')" in binds
    assert "https://graph.microsoft.com/v1.0/users('them@co.com')" in binds
    # aadUserConversationMember with an owner role is required by Graph
    assert all(m["@odata.type"] == "#microsoft.graph.aadUserConversationMember"
               for m in p["members"])
    assert all("owner" in m["roles"] for m in p["members"])


def test_chat_create_payload_group_when_multiple_recipients():
    p = g.build_chat_create_payload("me@co.com", ["a@co.com", "b@co.com"])
    assert p["chatType"] == "group"
    assert len(p["members"]) == 3


def test_chat_message_payload_shape():
    assert g.build_chat_message_payload("ping") == {
        "body": {"contentType": "text", "content": "ping"}}
    assert g.build_chat_message_payload("<i>x</i>", html=True)["body"]["contentType"] == "html"


# ── Impure send paths: dry-run + missing-mgc guard ───────────────────────────

def test_send_email_dry_run_returns_payload_without_shelling():
    out = g.send_email(["a@x.com"], "S", "B", dry_run=True)
    assert out["dry_run"] is True
    assert out["payload"]["message"]["subject"] == "S"


def test_send_teams_dry_run_returns_payloads_without_shelling():
    out = g.send_teams("me@co.com", ["them@co.com"], "hi", dry_run=True)
    assert out["dry_run"] is True
    assert out["chat"]["chatType"] == "oneOnOne"
    assert out["message"]["body"]["content"] == "hi"


def test_missing_mgc_raises_actionable_error(monkeypatch):
    monkeypatch.setattr(g.shutil, "which", lambda _: None)
    try:
        g._run_mgc(["users", "send-mail", "post", "--body", "{}"])
        assert False, "expected RuntimeError when mgc is absent"
    except RuntimeError as e:
        assert "mgc" in str(e).lower()
        assert "login" in str(e).lower()  # points at the auth/login remedy


def test_unified_scopes_cover_email_and_teams_and_calendar():
    s = g.MGC_SCOPES
    assert "Mail.Send" in s
    assert "Chat.ReadWrite" in s
    assert "Calendars.ReadWrite" in s


# ── Real mgc argv (the actual send command — regression guard for C1/C2) ─────

class _FakeProc:
    def __init__(self, stdout="", rc=0):
        self.stdout, self.stderr, self.returncode = stdout, "", rc


def test_send_email_argv_requires_user_id_me(monkeypatch):
    """sendMail under the users resource REQUIRES --user-id (verified vs mgc 1.9.0)."""
    calls = []
    monkeypatch.setattr(g.shutil, "which", lambda _: "/usr/bin/mgc")
    monkeypatch.setattr(g.subprocess, "run", lambda args, **k: calls.append(args) or _FakeProc(""))
    g.send_email(["a@x.com"], "S", "B")
    argv = calls[0]
    assert argv[:4] == ["mgc", "users", "send-mail", "post"]
    assert "--user-id" in argv and argv[argv.index("--user-id") + 1] == "me"
    assert "--body" in argv


def test_send_teams_message_uses_chat_id_option_not_positional(monkeypatch):
    """The chat id is a REQUIRED --chat-id option, not a path segment (verified vs mgc 1.9.0)."""
    import json as _json
    calls = []
    def fake_run(args, **k):
        calls.append(args)
        return _FakeProc(_json.dumps({"id": "CHAT-1" if "messages" not in args else "MSG-1"}))
    monkeypatch.setattr(g.shutil, "which", lambda _: "/usr/bin/mgc")
    monkeypatch.setattr(g.subprocess, "run", fake_run)
    out = g.send_teams("me@co.com", ["them@co.com"], "ping")
    create_argv, message_argv = calls[0], calls[1]
    assert create_argv[:3] == ["mgc", "chats", "create"]
    assert message_argv[:4] == ["mgc", "chats", "messages", "create"]
    assert message_argv[message_argv.index("--chat-id") + 1] == "CHAT-1"
    assert out["message_id"] == "MSG-1"


# ── Attachments (inc5 slice 9) ───────────────────────────────────────────────
# Email carries a base64 fileAttachment; Teams carries a reference attachment
# (a hosted URL) — Graph has NO base64 path for chat files.

def test_email_payload_includes_base64_file_attachment(tmp_path):
    f = tmp_path / "digest.docx"
    f.write_bytes(b"PK\x03\x04fake-docx-bytes")
    p = g.build_email_payload(["a@x.com"], "S", "B", attachments=[str(f)])
    atts = p["message"]["attachments"]
    assert len(atts) == 1
    a = atts[0]
    assert a["@odata.type"] == "#microsoft.graph.fileAttachment"
    assert a["name"] == "digest.docx"
    assert a["contentType"] == \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert base64.b64decode(a["contentBytes"]) == b"PK\x03\x04fake-docx-bytes"


def test_email_payload_no_attachments_key_when_empty():
    # Back-compat: no attachments arg, or an empty list, leaves the payload clean.
    assert "attachments" not in g.build_email_payload(["a@x.com"], "S", "B")["message"]
    assert "attachments" not in \
        g.build_email_payload(["a@x.com"], "S", "B", attachments=[])["message"]


def test_chat_payload_reference_attachment_forces_html_and_links():
    p = g.build_chat_message_payload(
        "See attached.",
        attachments=[{"name": "digest.docx", "url": "https://sp/sites/PM-OS/digest.docx"}])
    # A chat attachment reference must ride an HTML body that references it by id.
    assert p["body"]["contentType"] == "html"
    atts = p["attachments"]
    assert len(atts) == 1
    a = atts[0]
    assert a["contentType"] == "reference"
    assert a["contentUrl"] == "https://sp/sites/PM-OS/digest.docx"
    assert a["name"] == "digest.docx"
    assert f'<attachment id="{a["id"]}">' in p["body"]["content"]


def test_chat_payload_no_attachments_unchanged():
    # Back-compat: the no-attachment shape is byte-for-byte what it was.
    assert g.build_chat_message_payload("ping") == {
        "body": {"contentType": "text", "content": "ping"}}


def test_send_email_dry_run_carries_attachments(tmp_path):
    f = tmp_path / "a.docx"
    f.write_bytes(b"x")
    out = g.send_email(["a@x.com"], "S", "B", attachments=[str(f)], dry_run=True)
    assert out["payload"]["message"]["attachments"][0]["name"] == "a.docx"


def test_send_teams_dry_run_carries_reference_attachments():
    out = g.send_teams("me@co.com", ["them@co.com"], "hi",
                       attachments=[{"name": "a.docx", "url": "https://sp/a.docx"}],
                       dry_run=True)
    assert out["message"]["attachments"][0]["contentUrl"] == "https://sp/a.docx"
