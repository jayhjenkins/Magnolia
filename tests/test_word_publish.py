"""Word publish is menu-only: the ONLY .docx push is POST /api/tasks/{id}/output/word.

Covers the new route (Tier-2 confirm, publish vs sync, stamping), the GET output
"word" field, enrichment gating (no URL unless the .docx exists), and the removal
of the auto-push on task completion. doc_sync is always monkeypatched - nothing
here touches a real OneDrive or the real manifest.
"""
import io
import json
import os
import subprocess
from types import SimpleNamespace

import pytest


class _FakeHandler:
    def __init__(self, body=None, raw=None):
        if raw is not None:
            self._body = raw
        else:
            self._body = json.dumps(body).encode("utf-8") if body is not None else b""
        self.headers = {"Content-Length": str(len(self._body))}
        self.status = None
        self._chunks = []
        self.rfile = io.BytesIO(self._body)
        self.close_connection = False

    def body_consumed(self):
        return self.rfile.tell() == len(self._body)

    def send_response(self, s): self.status = s
    def send_header(self, *a): pass
    def end_headers(self): pass
    @property
    def wfile(self): return self
    def write(self, b): self._chunks.append(b)
    def json(self): return json.loads(b"".join(self._chunks).decode("utf-8"))


def _seed_task_with_output(tasks_root, rel_path, content):
    import task_lib
    tid, _ = task_lib.create_task("Competitive landscape brief", queue="agent", domain="product")
    task_lib.update_task(tid, changes={"agent_output": rel_path})
    abspath = os.path.join(tasks_root, rel_path)
    os.makedirs(os.path.dirname(abspath), exist_ok=True)
    with open(abspath, "w", encoding="utf-8") as f:
        f.write(content)
    return tid

CONFIRM_MSG = ("Publishing creates a Word copy of this document in your OneDrive, "
               "where others with access can see it. Continue?")


@pytest.fixture
def srv(tasks_root, monkeypatch):
    import task_server
    monkeypatch.setattr(task_server, "PM_OS_DIR", tasks_root)
    return task_server


@pytest.fixture
def word(srv, tasks_root, monkeypatch, tmp_path):
    """Fake doc_sync seam: a dict of docx-on-disk state + recorded calls."""
    state = {"exists": set(), "synced": [], "confirmed": [], "integration": {}}
    od = tmp_path / "od"

    def _docx_for(p):
        return str(od / (os.path.basename(p)[:-3] + ".docx"))

    def word_status(p, config=None):
        state.setdefault("configs", []).append(config)
        if p in state["exists"]:
            return {"exists": True, "url": "https://sp/" + os.path.basename(_docx_for(p)),
                    "docx_path": _docx_for(p)}
        return {"exists": False, "url": None, "docx_path": None}

    def sync_one(p):
        state["synced"].append(p)
        state["exists"].add(p)
        return True

    monkeypatch.setattr(srv.doc_sync, "word_status", word_status)
    monkeypatch.setattr(srv.doc_sync, "sync_one", sync_one)
    monkeypatch.setattr(srv.doc_sync, "is_configured", lambda: True)
    state["loads"] = 0

    def _try_load_config():
        state["loads"] += 1
        return {"onedrive_root": str(od)}
    monkeypatch.setattr(srv.doc_sync, "_try_load_config", _try_load_config)
    monkeypatch.setattr(srv.profile_lib, "integration",
                        lambda name, root=None: dict(state["integration"]) if name == "doc_sync" else {})
    monkeypatch.setattr(srv.profile_lib, "set_integration_confirmed",
                        lambda cat, val, provider=None, root=None: state["confirmed"].append((cat, val)))
    return state


def _abs(tasks_root, rel):
    return os.path.realpath(os.path.join(tasks_root, rel))


REL = "datasets/product/agent-output/brief.md"


# ── GET /api/tasks/{id}/output carries "word" ────────────────────────────────

def test_get_output_includes_word_status(srv, tasks_root, word):
    tid = _seed_task_with_output(tasks_root, REL, "# Brief\n")
    h = _FakeHandler()
    srv.handle_get_output(h, tid)
    assert h.status == 200
    assert h.json()["word"] == {"exists": False, "url": None, "docx_path": None}
    word["exists"].add(_abs(tasks_root, REL))
    h = _FakeHandler()
    srv.handle_get_output(h, tid)
    assert h.json()["word"]["exists"] is True


# ── POST /api/tasks/{id}/output/word ─────────────────────────────────────────

def test_publish_word_400_when_unconfigured(srv, tasks_root, word, monkeypatch):
    monkeypatch.setattr(srv.doc_sync, "is_configured", lambda: False)
    tid = _seed_task_with_output(tasks_root, REL, "# Brief\n")
    h = _FakeHandler({"confirm": True})
    srv.handle_publish_word(h, tid)
    assert h.status == 400
    assert "error" in h.json()
    assert word["synced"] == []


def test_publish_word_409_needs_confirmation_first_time(srv, tasks_root, word):
    tid = _seed_task_with_output(tasks_root, REL, "# Brief\n")
    h = _FakeHandler({})
    srv.handle_publish_word(h, tid)
    assert h.status == 409
    assert h.json() == {"needs_confirmation": True, "message": CONFIRM_MSG}
    assert word["synced"] == [] and word["confirmed"] == []


def test_publish_word_confirm_publishes_and_stamps(srv, tasks_root, word):
    import task_lib
    tid = _seed_task_with_output(tasks_root, REL, "# Brief\n")
    h = _FakeHandler({"confirm": True})
    srv.handle_publish_word(h, tid)
    assert h.status == 200, h.json()
    resp = h.json()
    assert resp["ok"] is True and resp["action"] == "published"
    assert resp["word"]["exists"] is True
    assert word["confirmed"] == [("doc_sync", True)]
    assert word["synced"] == [_abs(tasks_root, REL)]
    fm = task_lib.read_task(tid)["frontmatter"]
    assert fm["sharepoint_url"] == resp["word"]["url"]
    assert fm["sharepoint_path"] == resp["word"]["docx_path"]


def test_publish_word_already_confirmed_syncs_existing(srv, tasks_root, word):
    word["integration"] = {"confirmed": True}
    word["exists"].add(_abs(tasks_root, REL))
    tid = _seed_task_with_output(tasks_root, REL, "# Brief\n")
    h = _FakeHandler({})
    srv.handle_publish_word(h, tid)
    assert h.status == 200
    assert h.json()["action"] == "synced"
    assert word["confirmed"] == []  # already consented - no re-write of the flag
    assert word["synced"] == [_abs(tasks_root, REL)]


def test_publish_word_500_when_sync_fails(srv, tasks_root, word, monkeypatch):
    word["integration"] = {"confirmed": True}
    def boom(p):
        raise RuntimeError("pandoc not found - install pandoc to enable Word sync")
    monkeypatch.setattr(srv.doc_sync, "sync_one", boom)
    tid = _seed_task_with_output(tasks_root, REL, "# Brief\n")
    h = _FakeHandler({})
    srv.handle_publish_word(h, tid)
    assert h.status == 500
    assert "pandoc" in h.json()["error"]


def test_publish_word_404_when_no_markdown_output(srv, tasks_root, word):
    import task_lib
    tid, _ = task_lib.create_task("no output", queue="agent")
    h = _FakeHandler({"confirm": True})
    srv.handle_publish_word(h, tid)
    assert h.status == 404


def test_publish_word_early_404_still_consumes_body(srv, tasks_root, word):
    """An early 404 must not leave the request body unread on a keep-alive
    connection (the next request would be parsed from the leftover bytes)."""
    import task_lib
    tid, _ = task_lib.create_task("no output", queue="agent")
    h = _FakeHandler({"confirm": True})
    srv.handle_publish_word(h, tid)
    assert h.status == 404
    assert h.body_consumed()

    h = _FakeHandler({"confirm": True})
    srv.handle_publish_word(h, "TASK-9999")  # task not found
    assert h.status == 404
    assert h.body_consumed()


def test_publish_word_invalid_json_400(srv, tasks_root, word):
    tid = _seed_task_with_output(tasks_root, REL, "# Brief\n")
    h = _FakeHandler(raw=b"{not json")
    srv.handle_publish_word(h, tid)
    assert h.status == 400
    assert h.body_consumed()
    assert word["synced"] == []


def test_publish_word_router_invalid_id_drains_body(srv, monkeypatch):
    """The router's early 400 for a bad task id drains the body too."""
    errors = []
    monkeypatch.setattr(srv, "_error_response",
                        lambda handler, msg, status=500: errors.append((status, msg)))
    h = _FakeHandler({"confirm": True})
    h.path = "/api/tasks/not-a-task/output/word"
    assert srv.TaskServerHandler._route_request(h, "POST") is True
    assert errors == [(400, "Invalid task ID format")]
    assert h.body_consumed() or h.close_connection


def test_publish_word_refuses_output_outside_datasets(srv, tasks_root, word):
    """Only files under PM_OS_DIR/datasets/ may be pushed to Word."""
    rel = "scripts/notes.md"
    tid = _seed_task_with_output(tasks_root, rel, "# secret\n")
    word["integration"] = {"confirmed": True}
    h = _FakeHandler({"confirm": True})
    srv.handle_publish_word(h, tid)
    assert h.status == 400
    msg = h.json()["error"]
    assert "datasets" in msg and msg.isascii()
    assert word["synced"] == []


def test_get_output_omits_word_outside_datasets(srv, tasks_root, word):
    tid = _seed_task_with_output(tasks_root, "scripts/notes.md", "# n\n")
    h = _FakeHandler()
    srv.handle_get_output(h, tid)
    assert h.status == 200
    assert "word" not in h.json()


def test_publish_word_has_module_lock(srv):
    import threading
    assert isinstance(srv._WORD_LOCK, type(threading.Lock()))


def test_list_tasks_loads_word_config_once(srv, tasks_root, word):
    import task_lib
    for i in range(3):
        tid, _ = task_lib.create_task(f"t{i}", queue="agent")
        task_lib.update_task(tid, changes={"agent_output": REL})
    word["loads"] = 0
    word["configs"] = []
    h = _FakeHandler()
    srv.handle_list_tasks(h, {})
    assert h.status == 200
    assert word["loads"] == 1
    assert len(word["configs"]) == 3
    assert all(c == {"onedrive_root": word["configs"][0]["onedrive_root"]} for c in word["configs"])


def test_list_activity_loads_word_config_once(srv, tasks_root, word):
    import task_lib
    for i in range(2):
        tid, _ = task_lib.create_task(f"t{i}", queue="agent")
        task_lib.update_task(tid, changes={"agent_output": REL})
        task_lib.complete_task(tid, output_path=REL)
    word["loads"] = 0
    word["configs"] = []
    h = _FakeHandler()
    srv.handle_list_activity(h, {})
    assert h.status == 200
    assert word["loads"] == 1
    assert len(word["configs"]) == 2 and all(c for c in word["configs"])


def test_publish_word_route_registered_before_generic():
    src = open(os.path.join(os.path.dirname(__file__), "..", "scripts", "task_server.py"),
               encoding="utf-8").read()
    assert src.index('/api/tasks/([^/]+)/output/word$') < src.index('^/api/tasks/([^/]+)$')


# ── _enrich_sharepoint_url only advertises a .docx that exists ───────────────

def test_enrich_no_url_when_docx_missing(srv, tasks_root, word):
    t = {"agent_output": REL}
    srv._enrich_sharepoint_url(t)
    assert "sharepoint_url" not in t


def test_enrich_url_from_agent_output_when_docx_exists(srv, tasks_root, word):
    word["exists"].add(_abs(tasks_root, REL))
    t = {"agent_output": REL}
    srv._enrich_sharepoint_url(t)
    assert t["sharepoint_url"] == "https://sp/brief.docx"


def test_enrich_drops_stale_fields_when_docx_missing(srv, tasks_root, word, tmp_path):
    t = {"agent_output": REL, "sharepoint_path": str(tmp_path / "gone.docx"),
         "sharepoint_url": "https://sp/gone.docx"}
    srv._enrich_sharepoint_url(t)
    assert "sharepoint_url" not in t and "sharepoint_path" not in t


def test_enrich_keeps_url_when_sharepoint_path_exists(srv, tasks_root, word, tmp_path):
    docx = tmp_path / "here.docx"
    docx.write_bytes(b"PK")
    t = {"sharepoint_path": str(docx), "sharepoint_url": "https://sp/here.docx"}
    srv._enrich_sharepoint_url(t)
    assert t["sharepoint_url"] == "https://sp/here.docx"


# ── Completion never auto-pushes ─────────────────────────────────────────────

def test_complete_task_does_not_stamp_or_push(tasks_root, monkeypatch):
    import task_lib
    popens = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popens.append(a))
    monkeypatch.setattr(task_lib, "_sharepoint_url", lambda p: "https://sp/x.docx")
    tid, _ = task_lib.create_task("t", queue="agent")
    task_lib.complete_task(tid, output_path=REL)
    fm = task_lib.read_task(tid)["frontmatter"]
    assert fm["agent_output"] == REL
    assert "sharepoint_path" not in fm and "sharepoint_url" not in fm
    assert popens == []
    assert not hasattr(task_lib, "_trigger_doc_sync")


def test_agent_complete_does_not_stamp_or_push(tasks_root, monkeypatch):
    import task_cli, task_lib
    popens = []
    monkeypatch.setattr(task_cli, "_spawn_judge", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popens.append(a))
    monkeypatch.setattr(task_lib, "_sharepoint_url", lambda p: "https://sp/x.docx")
    tid, _ = task_lib.create_task("t", queue="agent")
    task_cli.cmd_agent_complete(SimpleNamespace(task_id=tid, output=REL))
    fm = task_lib.read_task(tid)["frontmatter"]
    assert fm["agent_output"] == REL
    assert "sharepoint_path" not in fm and "sharepoint_url" not in fm
    assert popens == []
