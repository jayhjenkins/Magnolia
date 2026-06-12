"""Tests for the Adapt API endpoints in scripts/task_server.py.

Handler-level: the `_FakeHandler` mirrors test_quick_add_route / the now-feed
filter tests. The runner boundary (adapt_runner.run_turn) is mocked to a canned
generator; live_runs is used FOR REAL with a canned source (it is pure stdlib
threading, fast and deterministic here); adaptations_lib + adapt_transcript run
against a temp STORE_DIR so the rail CRUD and the durable event log are
exercised end to end. The git seam for the bundle delete is mocked so we assert
revert ORDER without touching the real repo.
"""
import json
import time

import pytest


class _FakeHandler:
    """Captures status + JSON for unary handlers, and SSE frames for streams."""

    def __init__(self, body=None):
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        self._body = raw
        self.headers = {"Content-Length": str(len(raw))}
        self.status = None
        self._chunks = []
        self.closed = False

    @property
    def rfile(self):
        import io
        return io.BytesIO(self._body)

    def send_response(self, s): self.status = s
    def send_header(self, *a): pass
    def end_headers(self): pass

    @property
    def wfile(self): return self

    def write(self, b): self._chunks.append(b)
    def flush(self): pass

    def json(self):
        return json.loads(b"".join(self._chunks).decode("utf-8"))

    def sse_frames(self):
        """Parse the SSE body into a list of decoded `data:` JSON objects."""
        text = b"".join(self._chunks).decode("utf-8")
        frames = []
        for block in text.split("\n\n"):
            block = block.strip()
            if not block or block.startswith("event:") and "data: {}" in block:
                # the `event: done` sentinel block
                continue
            for line in block.splitlines():
                if line.startswith("data: "):
                    payload = line[len("data: "):]
                    try:
                        frames.append(json.loads(payload))
                    except ValueError:
                        pass
        return frames


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point adaptations_lib + adapt_transcript at a temp store dir."""
    import adaptations_lib
    import adapt_transcript
    d = tmp_path / "adaptations"
    d.mkdir()
    monkeypatch.setattr(adaptations_lib, "STORE_DIR", str(d))
    monkeypatch.setattr(adapt_transcript, "STORE_DIR", str(d))
    return d


@pytest.fixture
def srv(store, monkeypatch):
    import task_server
    import live_runs
    live_runs._reset()
    return task_server


# --- GET /api/adaptations (rail data) ----------------------------------------

def test_list_adaptations_returns_payload(srv):
    import adaptations_lib
    # Rows start `pending` (hidden); promote them off pending so they appear in
    # the rail (a build landing is what promotes a row in production).
    a = adaptations_lib.create("Churn worker", "sid-1")
    adaptations_lib.set_state(a, "on")
    b = adaptations_lib.create("Shopify adapter", "sid-2")
    adaptations_lib.set_state(b, "off")

    h = _FakeHandler()
    srv.handle_list_adaptations(h)
    assert h.status == 200
    payload = h.json()
    assert "adaptations" in payload
    ids = {r["id"] for r in payload["adaptations"]}
    assert ids == {a, b}
    by_id = {r["id"]: r for r in payload["adaptations"]}
    assert by_id[a]["state"] == "on"
    assert by_id[a]["name"] == "Churn worker"


# --- PUT /api/adaptations/{id}/toggle ----------------------------------------

def test_toggle_flips_state(srv):
    import adaptations_lib
    a = adaptations_lib.create("X", "sid")
    h = _FakeHandler({"state": "on"})
    srv.handle_toggle_adaptation(h, a)
    assert h.status == 200
    assert h.json() == {"ok": True, "state": "on"}
    assert adaptations_lib.read(a)["state"] == "on"


def test_toggle_rejects_invalid_state(srv):
    import adaptations_lib
    a = adaptations_lib.create("X", "sid")
    h = _FakeHandler({"state": "sideways"})
    srv.handle_toggle_adaptation(h, a)
    assert h.status == 400
    assert "error" in h.json()
    # unchanged - a freshly-created row is the hidden keying state `pending`
    assert adaptations_lib.read(a)["state"] == "pending"


def test_toggle_rejects_building_via_endpoint(srv):
    """Only on/off are user-toggleable; building/pending are runner-internal."""
    import adaptations_lib
    a = adaptations_lib.create("X", "sid")
    h = _FakeHandler({"state": "building"})
    srv.handle_toggle_adaptation(h, a)
    assert h.status == 400


# --- PUT /api/adaptations/{id} (rename) --------------------------------------

def test_rename_adaptation(srv):
    import adaptations_lib
    a = adaptations_lib.create("Old name", "sid")
    h = _FakeHandler({"name": "New shiny name"})
    srv.handle_rename_adaptation(h, a)
    assert h.status == 200
    assert h.json()["ok"] is True
    assert adaptations_lib.read(a)["name"] == "New shiny name"


def test_rename_rejects_empty_name(srv):
    import adaptations_lib
    a = adaptations_lib.create("Old", "sid")
    h = _FakeHandler({"name": "   "})
    srv.handle_rename_adaptation(h, a)
    assert h.status == 400


# --- POST /api/adapt (new build, decoupled SSE) ------------------------------

# NOTE on mocking the runner: the REAL adapt_runner.run_turn appends every
# user-visible event to adapt_transcript itself (the durable log the handler
# tails). The handler deliberately passes a NO-OP append_fn to live_runs to
# avoid double-logging. So a faithful canned runner must ALSO append to
# adapt_transcript as it yields - otherwise the tail (which reads the log) sees
# nothing. This helper does exactly that.

def _persisting_runner(events):
    import adapt_transcript

    def _gen(adaptation_id, message):
        for e in events:
            adapt_transcript.append_event(adaptation_id, e)
            yield e
    return _gen


def test_post_adapt_new_build_creates_row_runs_and_streams(srv, monkeypatch):
    """A new build: the handler creates the row, keys the run by its id, and
    streams the events including the adaptation event carrying that id."""
    import adapt_runner
    import adapt_transcript
    import adaptations_lib

    captured = {}

    def fake_run_turn(adaptation_id, message):
        captured["adaptation_id"] = adaptation_id
        captured["message"] = message
        # Mirror the real runner: persist each user-visible event to the durable
        # log (the handler tails that), then yield it. The runner announces the
        # row (state building) first, then a text event, then flips to off.
        for e in (
            {"kind": "adaptation", "adaptation_id": adaptation_id,
             "name": "prov", "state": "building"},
            {"kind": "text", "role": "assistant", "text": "Built it."},
            {"kind": "adaptation", "adaptation_id": adaptation_id,
             "name": "prov", "state": "off"},
        ):
            adapt_transcript.append_event(adaptation_id, e)
            yield e

    monkeypatch.setattr(adapt_runner, "run_turn", fake_run_turn)

    h = _FakeHandler({"message": "Build a churn-risk worker"})
    srv.handle_adapt(h)

    assert h.status == 200
    # A row was created at POST time, keyed, and passed to run_turn.
    assert captured["adaptation_id"] is not None
    rid = captured["adaptation_id"]
    assert adaptations_lib.read(rid)["name"].startswith("Build a churn-risk")
    # The POST-created row is the hidden `pending` keying state - it is NOT
    # surfaced in the rail until a build lands and the runner promotes it.
    assert adaptations_lib.read(rid)["state"] == "pending"
    assert rid not in {r["id"] for r in adaptations_lib.list_all()}

    frames = h.sse_frames()
    adapt_frames = [f for f in frames if f.get("kind") == "adaptation"]
    assert any(f["adaptation_id"] == rid for f in adapt_frames)
    assert any(f.get("kind") == "text" for f in frames)


def test_post_adapt_concurrent_same_key_returns_409(srv, monkeypatch):
    """While a live run exists for the key, a NEW POST for the same id is 409.
    The first POST creates the row; we drive a second POST naming that id."""
    import adapt_runner
    import live_runs

    rid_holder = {}

    started = {"go": False}

    def slow_run_turn(adaptation_id, message):
        rid_holder["id"] = adaptation_id
        yield {"kind": "adaptation", "adaptation_id": adaptation_id,
               "name": "p", "state": "building"}
        # Keep the run "live" until the test releases it.
        while not started["go"]:
            time.sleep(0.01)
        yield {"kind": "text", "text": "done"}

    monkeypatch.setattr(adapt_runner, "run_turn", slow_run_turn)

    # First POST: starts the run. Drive it in a thread so it parks mid-run.
    import threading
    h1 = _FakeHandler({"message": "Build the thing"})
    t = threading.Thread(target=srv.handle_adapt, args=(h1,))
    t.start()

    # Wait for the run to register as live.
    deadline = time.time() + 2
    while "id" not in rid_holder and time.time() < deadline:
        time.sleep(0.01)
    rid = rid_holder["id"]
    while not live_runs.is_live(rid) and time.time() < deadline:
        time.sleep(0.01)
    assert live_runs.is_live(rid)

    # Second POST naming the SAME id while live -> 409.
    h2 = _FakeHandler({"message": "again", "adaptation_id": rid})
    srv.handle_adapt(h2)
    assert h2.status == 409

    # Release the first run and join.
    started["go"] = True
    t.join(timeout=2)


def test_post_adapt_rejects_empty_message(srv):
    h = _FakeHandler({"message": "   "})
    srv.handle_adapt(h)
    assert h.status == 400


def test_post_adapt_surfaces_run_error_as_terminal_frame(srv, monkeypatch):
    """When the source run errors, after the tail drains the stream ends with a
    terminal error frame (sourced from live_runs.run_error)."""
    import adapt_runner

    import adapt_transcript

    def boom_run_turn(adaptation_id, message):
        e = {"kind": "adaptation", "adaptation_id": adaptation_id,
             "name": "p", "state": "building"}
        adapt_transcript.append_event(adaptation_id, e)
        yield e
        raise RuntimeError("build crashed")

    monkeypatch.setattr(adapt_runner, "run_turn", boom_run_turn)

    h = _FakeHandler({"message": "Build something that explodes"})
    srv.handle_adapt(h)

    assert h.status == 200
    frames = h.sse_frames()
    err = [f for f in frames if f.get("kind") == "error"]
    assert len(err) == 1
    assert "unexpected" in err[0]["text"].lower() or "retry" in err[0]["text"].lower()


# --- GET /api/adapt/stream?adaptation=<id> (reconnect/replay+tail) -----------

def test_get_adapt_stream_replays_event_log(srv, monkeypatch):
    """The GET stream replays the durable event log for a finished run and ends
    cleanly (no live run needed)."""
    import adaptations_lib
    import adapt_transcript

    rid = adaptations_lib.create("Existing", "sid")
    adapt_transcript.append_event(rid, {"kind": "text", "text": "one"})
    adapt_transcript.append_event(rid, {"kind": "text", "text": "two"})

    h = _FakeHandler()
    srv.handle_adapt_stream(h, {"adaptation": [rid]})
    assert h.status == 200
    frames = h.sse_frames()
    texts = [f["text"] for f in frames if f.get("kind") == "text"]
    assert texts == ["one", "two"]


def test_get_adapt_stream_requires_adaptation_param(srv):
    h = _FakeHandler()
    srv.handle_adapt_stream(h, {})
    assert h.status == 400


def test_get_adapt_stream_surfaces_run_error(srv, monkeypatch):
    """A reconnect to a run that ended in error replays the log then emits the
    terminal error frame."""
    import adaptations_lib
    import adapt_transcript
    import live_runs

    rid = adaptations_lib.create("Crashed", "sid")
    adapt_transcript.append_event(rid, {"kind": "text", "text": "partial"})

    # Simulate a finished-with-error run for this key.
    def boom():
        raise RuntimeError("crashed")
        yield  # pragma: no cover

    live_runs.start(rid, boom(), lambda e: None)
    deadline = time.time() + 2
    while live_runs.is_live(rid) and time.time() < deadline:
        time.sleep(0.01)
    assert live_runs.run_error(rid) is not None

    h = _FakeHandler()
    srv.handle_adapt_stream(h, {"adaptation": [rid]})
    frames = h.sse_frames()
    assert any(f.get("kind") == "text" and f.get("text") == "partial" for f in frames)
    assert any(f.get("kind") == "error" for f in frames)


# --- POST /api/adaptations/{id}/delete (bundle revert + tombstone) -----------

def test_delete_reverts_manifest_commits_newest_first_and_tombstones(srv, monkeypatch):
    import adaptations_lib

    rid = adaptations_lib.create("Bundle", "sid")
    # Capture order (oldest -> newest): c1, c2, c3.
    adaptations_lib.add_artifact(rid, "worker", "scripts/workers/a.md", "c1")
    adaptations_lib.add_artifact(rid, "adapter", "fam/prov", "c2")
    adaptations_lib.add_artifact(rid, "card-type", "alert", "c3")

    reverts = []
    monkeypatch.setattr(srv, "_adapt_git_revert",
                        lambda sha: reverts.append(sha) or (True, ""))

    h = _FakeHandler({})
    srv.handle_delete_adaptation(h, rid)
    assert h.status == 200
    assert h.json()["ok"] is True

    # Reverted NEWEST-first (reverse of capture order).
    assert reverts == ["c3", "c2", "c1"]
    # Row tombstoned (no longer in the active list).
    assert rid not in {r["id"] for r in adaptations_lib.list_all()}


def test_delete_tolerates_a_conflicting_revert(srv, monkeypatch):
    """A revert that fails (conflict/already-reverted) does not 500; the delete
    proceeds best-effort and still tombstones, reporting partial failure."""
    import adaptations_lib

    rid = adaptations_lib.create("Bundle2", "sid")
    adaptations_lib.add_artifact(rid, "worker", "scripts/workers/a.md", "c1")
    adaptations_lib.add_artifact(rid, "worker", "scripts/workers/b.md", "c2")

    def flaky(sha):
        if sha == "c2":
            return (False, "later changes conflict")
        return (True, "")

    monkeypatch.setattr(srv, "_adapt_git_revert", flaky)

    h = _FakeHandler({})
    srv.handle_delete_adaptation(h, rid)
    # Not a 500; clear signal to the operator.
    assert h.status == 200
    body = h.json()
    assert body["ok"] is True
    assert body.get("partial") is True
    # Still tombstoned.
    assert rid not in {r["id"] for r in adaptations_lib.list_all()}


def test_delete_with_empty_manifest_just_tombstones(srv, monkeypatch):
    import adaptations_lib
    rid = adaptations_lib.create("Empty", "sid")
    reverts = []
    monkeypatch.setattr(srv, "_adapt_git_revert",
                        lambda sha: reverts.append(sha) or (True, ""))
    h = _FakeHandler({})
    srv.handle_delete_adaptation(h, rid)
    assert h.status == 200
    assert reverts == []
    assert rid not in {r["id"] for r in adaptations_lib.list_all()}
