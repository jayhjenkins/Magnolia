"""Task 4 — send-message send path: draft build, gated send, manual fallback,
and the messaging confirm re-drive. Mirrors test_publish_core (core fns tested
directly with adapters.publish monkeypatched; no real mgc / HTTP server)."""
import json

import pytest

import shipper


@pytest.fixture
def srv(tasks_root, profile_root, monkeypatch):
    """task_server with task_lib on the temp tree and profile_lib reads at profile_root."""
    import task_server, profile_lib

    def _wrap(orig):
        def wrapper(*a, **k):
            if len(a) < 2 and "root" not in k:
                k = {**k, "root": profile_root}
            return orig(*a, **k)
        return wrapper

    for fn in ("provider", "integration"):
        monkeypatch.setattr(profile_lib, fn, _wrap(getattr(profile_lib, fn)))
    return task_server


def _send_task(channel="Teams", to="Dana", subject="", body="hello there"):
    import task_lib
    tid, _ = task_lib.create_task("send msg", queue="collab", domain="ops", creator="agent")
    task_lib.update_task(tid, {
        "task_type": "send-message", "message_channel": channel,
        "message_to": to, "message_subject": subject, "message_body": body})
    return tid


class _FakeHandler:
    def __init__(self):
        self.status = None
        self._chunks = []
    def send_response(self, s): self.status = s
    def send_header(self, *a): pass
    def end_headers(self): pass
    @property
    def wfile(self): return self
    def write(self, b): self._chunks.append(b)
    def json(self): return json.loads(b"".join(self._chunks).decode("utf-8"))


# ── draft build ──────────────────────────────────────────────────────────────

def test_message_draft_carries_attachments(srv, monkeypatch):
    monkeypatch.setattr(shipper, "_load_email_cache", lambda: {})
    import task_lib
    tid = _send_task(channel="Email", to="x@y.com", body="b")
    task_lib.update_task(tid, {"attachments": ["datasets/programs/artifacts/PROG-1/x.md"]})
    d = shipper._message_draft_from_task(tid)
    assert d["attachments"] == ["datasets/programs/artifacts/PROG-1/x.md"]


def test_message_draft_attachments_default_empty(srv, monkeypatch):
    monkeypatch.setattr(shipper, "_load_email_cache", lambda: {})
    d = shipper._message_draft_from_task(_send_task(channel="Email", to="x@y.com"))
    assert d["attachments"] == []


def test_message_draft_resolves_recipient_and_channel(srv, monkeypatch):
    monkeypatch.setattr(shipper, "_load_email_cache", lambda: {"Dana": "dana@co.com"})
    d = shipper._message_draft_from_task(_send_task(channel="Teams", to="Dana", body="ping"))
    assert d["channel"] == "teams"
    assert d["to"] == ["dana@co.com"]
    assert d["to_display"] == "Dana"
    assert d["body"] == "ping"


def test_message_draft_email_keeps_subject_and_literal_address(srv, monkeypatch):
    monkeypatch.setattr(shipper, "_load_email_cache", lambda: {})
    d = shipper._message_draft_from_task(_send_task(channel="Email", to="x@y.com", subject="Hi", body="b"))
    assert d["channel"] == "email" and d["subject"] == "Hi" and d["to"] == ["x@y.com"]


# ── gated send core ──────────────────────────────────────────────────────────

def _draft():
    return {"channel": "teams", "to": ["a@b.com"], "to_display": "A", "body": "x", "task_id": "T"}


def test_attempt_send_needs_confirm(srv, monkeypatch):
    import adapters
    monkeypatch.setattr(adapters, "publish",
                        lambda *a, **k: (_ for _ in ()).throw(adapters.NeedsConfirmation("messaging")))
    status, _ = srv._attempt_send_message(_send_task(), _draft())
    assert status == "needs_confirm"


def test_attempt_send_ok_records_message_id_and_completes(srv, monkeypatch):
    import adapters, task_lib
    monkeypatch.setattr(adapters, "publish", lambda *a, **k: ("MSG-1", None))
    tid = _send_task()
    status, payload = srv._attempt_send_message(tid, _draft())
    assert status == "ok" and payload == ("MSG-1", None)
    fm = task_lib.read_task(tid)["frontmatter"]
    assert fm["message_sent_at"] and fm.get("message_id") == "MSG-1"
    assert fm["status"] == "done"


def test_attempt_send_unconfigured_when_publish_returns_none(srv, monkeypatch):
    import adapters
    monkeypatch.setattr(adapters, "publish", lambda *a, **k: None)
    status, _ = srv._attempt_send_message(_send_task(), _draft())
    assert status == "unconfigured"


def test_attempt_send_skips_when_already_sent(srv, monkeypatch):
    import adapters, task_lib
    calls = []
    monkeypatch.setattr(adapters, "publish", lambda *a, **k: calls.append(1) or ("M", None))
    tid = _send_task()
    task_lib.complete_task(tid, actor="human")
    status, _ = srv._attempt_send_message(tid, _draft())
    assert status == "already_sent" and calls == []


def test_attempt_send_skips_when_message_sent_at_present(srv, monkeypatch):
    # H2: message_sent_at alone (archive may have failed) still blocks a re-send.
    import adapters, task_lib
    calls = []
    monkeypatch.setattr(adapters, "publish", lambda *a, **k: calls.append(1) or ("M", None))
    tid = _send_task()
    task_lib.update_task(tid, {"message_sent_at": task_lib._now_iso()})  # sent but not archived
    status, _ = srv._attempt_send_message(tid, _draft())
    assert status == "already_sent" and calls == []


def test_attempt_send_blocks_unresolved_bare_name(srv, monkeypatch):
    # H1: a recipient that didn't resolve to an address (no '@') must NOT be sent.
    import adapters
    monkeypatch.setattr(adapters, "get", lambda fam, root=None: object())  # provider configured
    calls = []
    monkeypatch.setattr(adapters, "publish", lambda *a, **k: calls.append(1) or ("X", None))
    draft = {"channel": "email", "to": ["Dana Smith"], "to_display": "Dana Smith", "body": "x"}
    status, payload = srv._attempt_send_message(_send_task(), draft)
    assert status == "error" and payload[0] == 400
    assert calls == []   # never attempted to send to a malformed address


def test_attempt_send_runtime_error_is_502(srv, monkeypatch):
    import adapters
    monkeypatch.setattr(adapters, "publish",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Authentication error. Run: mgc login")))
    status, payload = srv._attempt_send_message(_send_task(), _draft())
    assert status == "error" and payload[0] == 502


# ── manual fallback (no provider) ────────────────────────────────────────────

def test_record_manual_send_archives(srv):
    import task_lib
    tid = _send_task()
    srv._record_manual_send(tid)
    fm = task_lib.read_task(tid)["frontmatter"]
    assert fm["message_sent_at"] and fm["status"] == "done"


# ── confirm re-drive dispatches to messaging ─────────────────────────────────

def test_confirm_redrives_messaging_send(srv, monkeypatch):
    import adapters, task_lib, profile_lib
    seen = {}
    monkeypatch.setattr(adapters, "publish",
                        lambda fam, draft, **k: seen.update(fam=fam, ch=draft["channel"]) or ("MSG-9", None))
    monkeypatch.setattr(shipper, "_load_email_cache", lambda: {"Dana": "dana@co.com"})
    monkeypatch.setattr(profile_lib, "set_integration_confirmed", lambda *a, **k: None)

    src = _send_task(channel="Teams", to="Dana", body="ping")
    cid = srv._emit_confirm_card("messaging", src)
    h = _FakeHandler()
    srv.handle_confirm(h, cid)

    assert h.status == 200
    assert seen["fam"] == "messaging" and seen["ch"] == "teams"
    assert task_lib.read_task(src)["frontmatter"]["status"] == "done"   # source sent + archived
    assert task_lib.read_task(cid)["frontmatter"]["status"] == "done"   # confirm card cleared
