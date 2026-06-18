"""Task 2 — the messaging adapter family (m365 provider).

The provider dispatches by channel to the mgc seam (send_message_graph); here we
mock that seam so no real mgc runs, and assert dispatch + the (id, url) contract.
"""
import pytest

from adapters.messaging import m365
from adapters.messaging._contract import NotConfigured
import send_message_graph as graph


def test_is_configured_tracks_mgc_presence(monkeypatch):
    monkeypatch.setattr(m365.shutil, "which", lambda _: "/usr/bin/mgc")
    assert m365.is_configured() is True
    monkeypatch.setattr(m365.shutil, "which", lambda _: None)
    assert m365.is_configured() is False


def test_publish_email_dispatches_to_sendmail(monkeypatch):
    monkeypatch.setattr(m365.shutil, "which", lambda _: "/usr/bin/mgc")
    seen = {}
    monkeypatch.setattr(graph, "send_email",
                        lambda to, subj, body, **k: seen.update(to=to, subj=subj, body=body) or {"status": "sent"})
    draft = {"channel": "email", "to": ["a@x.com"], "subject": "Hi", "body": "Hello"}
    msg_id, url = m365.publish(draft)
    assert seen == {"to": ["a@x.com"], "subj": "Hi", "body": "Hello"}
    assert msg_id and url is None


def test_publish_teams_resolves_me_and_dispatches(monkeypatch):
    monkeypatch.setattr(m365.shutil, "which", lambda _: "/usr/bin/mgc")
    monkeypatch.setattr(m365, "_resolve_me_upn", lambda: "me@co.com")
    seen = {}
    monkeypatch.setattr(graph, "send_teams",
                        lambda me, to, body, **k: seen.update(me=me, to=to, body=body) or {"message_id": "MSG-1"})
    draft = {"channel": "teams", "to": ["them@co.com"], "body": "ping"}
    msg_id, url = m365.publish(draft)
    assert seen == {"me": "me@co.com", "to": ["them@co.com"], "body": "ping"}
    assert msg_id == "MSG-1" and url is None


def test_publish_unknown_channel_raises(monkeypatch):
    monkeypatch.setattr(m365.shutil, "which", lambda _: "/usr/bin/mgc")
    with pytest.raises(NotConfigured):
        m365.publish({"channel": "carrier-pigeon", "to": ["x"], "body": "y"})


def test_publish_without_mgc_raises_not_configured(monkeypatch):
    monkeypatch.setattr(m365.shutil, "which", lambda _: None)
    with pytest.raises(NotConfigured):
        m365.publish({"channel": "email", "to": ["a@x.com"], "body": "y"})


# ── Attachments degrade ladder (inc5 slice 9) ────────────────────────────────
# email: md -> temp docx (base64). teams: md -> docx in OneDrive -> SharePoint URL
# (reference). ANY failure degrades to an inline link in the body; never raises.

def test_publish_email_converts_md_attachment_to_docx(monkeypatch, tmp_path):
    monkeypatch.setattr(m365.shutil, "which", lambda _: "/usr/bin/mgc")
    md = tmp_path / "digest.md"
    md.write_text("# hi")
    monkeypatch.setattr(m365.doc_sync, "md_to_docx",
                        lambda src, dst: open(dst, "wb").write(b"PKdocx"))
    seen = {}
    monkeypatch.setattr(graph, "send_email",
                        lambda to, subj, body, **k: seen.update(atts=k.get("attachments"), body=body)
                        or {"status": "sent"})
    m365.publish({"channel": "email", "to": ["a@x.com"], "subject": "S",
                  "body": "B", "attachments": [str(md)]})
    assert seen["atts"] and seen["atts"][0].endswith(".docx")
    assert seen["body"] == "B"  # nothing degraded -> body unchanged


def test_publish_email_degrades_to_inline_link_when_conversion_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(m365.shutil, "which", lambda _: "/usr/bin/mgc")
    md = tmp_path / "digest.md"
    md.write_text("# hi")
    def boom(s, d):
        raise RuntimeError("pandoc not found")
    monkeypatch.setattr(m365.doc_sync, "md_to_docx", boom)
    seen = {}
    monkeypatch.setattr(graph, "send_email",
                        lambda to, subj, body, **k: seen.update(atts=k.get("attachments"), body=body)
                        or {"status": "sent"})
    m365.publish({"channel": "email", "to": ["a@x.com"], "subject": "S",
                  "body": "B", "attachments": [str(md)]})
    assert not seen["atts"]            # nothing attached
    assert str(md) in seen["body"]     # link inlined instead — never dropped silently


def test_publish_teams_references_sharepoint_url(monkeypatch, tmp_path):
    monkeypatch.setattr(m365.shutil, "which", lambda _: "/usr/bin/mgc")
    monkeypatch.setattr(m365, "_resolve_me_upn", lambda: "me@co.com")
    md = tmp_path / "digest.md"
    md.write_text("# hi")
    monkeypatch.setattr(m365.doc_sync, "sync_one", lambda p: None)
    monkeypatch.setattr(m365.doc_sync, "sharepoint_url_for", lambda p: "https://sp/digest.docx")
    seen = {}
    monkeypatch.setattr(graph, "send_teams",
                        lambda me, to, body, **k: seen.update(atts=k.get("attachments"), body=body)
                        or {"message_id": "M1"})
    m365.publish({"channel": "teams", "to": ["t@co.com"], "body": "B", "attachments": [str(md)]})
    assert seen["atts"][0]["url"] == "https://sp/digest.docx"
    assert seen["atts"][0]["name"] == "digest.md"


def test_publish_teams_degrades_when_no_url(monkeypatch, tmp_path):
    monkeypatch.setattr(m365.shutil, "which", lambda _: "/usr/bin/mgc")
    monkeypatch.setattr(m365, "_resolve_me_upn", lambda: "me@co.com")
    md = tmp_path / "digest.md"
    md.write_text("# hi")
    monkeypatch.setattr(m365.doc_sync, "sync_one", lambda p: None)
    monkeypatch.setattr(m365.doc_sync, "sharepoint_url_for", lambda p: None)
    seen = {}
    monkeypatch.setattr(graph, "send_teams",
                        lambda me, to, body, **k: seen.update(atts=k.get("attachments"), body=body)
                        or {"message_id": "M1"})
    m365.publish({"channel": "teams", "to": ["t@co.com"], "body": "B", "attachments": [str(md)]})
    assert not seen["atts"]
    assert str(md) in seen["body"]
