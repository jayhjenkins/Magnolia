"""Tests for the SSE chat route in task_server.py.

The task chat panel rides the SAME survive-disconnect substrate as Adapt:
chat_runner.run_turn is started on a live_runs daemon thread (keyed
`chat:<task_id>`) and the SSE handler only TAILS the durable chat transcript. So
a client disconnect stops only the tail — never the run — and a second
concurrent POST while the run is live returns 409 (live_runs.is_live), the same
status the old in-memory run-lock returned.

SSE + sockets are awkward to unit-test end-to-end, so these tests drive the
handler with a fake handler object rather than a real server socket. Because the
read side now tails the durable transcript, a faithful canned run_turn must ALSO
append each event to chat_transcript as it yields (mirroring the real runner and
the adapt-endpoint tests' _persisting_runner) — otherwise the tail sees nothing.
"""

import io
import json
import threading
import time

import pytest

import task_server
import task_lib
import chat_runner
import chat_transcript
import live_runs


# ─── Fake handler ──────────────────────────────────────────────────────────────

class FakeHandler:
    """Minimal stand-in for a BaseHTTPRequestHandler.

    Captures status, headers, and body writes; provides headers/rfile so
    _read_request_body works.
    """

    def __init__(self, body=None):
        raw = json.dumps(body or {}).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = {}
        # Ordered list of (key, value) so we can assert on duplicates — the
        # dict above collapses repeats and would hide a double-sent header.
        self.header_list = []
        self.ended = False

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers[key] = value
        self.header_list.append((key, value))

    def end_headers(self):
        # Mirror the real handler's overridden end_headers(), which injects the
        # CORS header into EVERY response before the base class finalizes them.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.ended = True

    def written(self):
        return self.wfile.getvalue().decode("utf-8")


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_runs():
    """Reset the live_runs registry around every test (chat now rides live_runs)."""
    live_runs._reset()
    yield
    live_runs._reset()


def _persisting_run_turn(events):
    """Build a canned chat_runner.run_turn that mirrors the real one: it appends
    each yielded event to chat_transcript (the durable log the handler tails)."""
    def _gen(task_id, message):
        for ev in events:
            chat_transcript.append_event(task_id, dict(ev))
            yield ev
    return _gen


def _wait_done(key, timeout=2.0):
    """Block until the live run for `key` is no longer live (or timeout)."""
    deadline = time.time() + timeout
    while live_runs.is_live(key) and time.time() < deadline:
        time.sleep(0.01)


# ─── handle_chat validation ─────────────────────────────────────────────────────

def test_handle_chat_rejects_empty_message(monkeypatch):
    called = {"run": False}

    def fake_run_turn(task_id, message):
        called["run"] = True
        yield {"kind": "text", "text": "x"}

    monkeypatch.setattr(chat_runner, "run_turn", fake_run_turn)

    handler = FakeHandler(body={"message": "   "})
    task_server.handle_chat(handler, "TASK-1")

    assert handler.status == 400
    assert called["run"] is False
    # No run started on a validation failure.
    assert not live_runs.is_live(task_server._chat_run_key("TASK-1"))


def test_handle_chat_409_when_chat_already_running(monkeypatch, tasks_root):
    """A second concurrent POST while a run is LIVE for the key returns 409 —
    the same status the old run-lock returned, now provided by live_runs.is_live."""
    monkeypatch.setattr(
        task_lib, "read_task",
        lambda tid: {"frontmatter": {"agent_status": "complete"}, "body": ""},
    )

    key = task_server._chat_run_key("TASK-1")
    started = {"go": False}

    def slow_run_turn(task_id, message):
        chat_transcript.append_event(task_id, {"kind": "text", "text": "first"})
        yield {"kind": "text", "text": "first"}
        # Keep the run live until the test releases it.
        while not started["go"]:
            time.sleep(0.01)
        yield {"kind": "result", "session_id": "s"}

    monkeypatch.setattr(chat_runner, "run_turn", slow_run_turn)

    # First POST: start the run in a thread so it parks mid-run.
    h1 = FakeHandler(body={"message": "hello"})
    t = threading.Thread(target=task_server.handle_chat, args=(h1, "TASK-1"))
    t.start()
    deadline = time.time() + 2
    while not live_runs.is_live(key) and time.time() < deadline:
        time.sleep(0.01)
    assert live_runs.is_live(key)

    # Second concurrent POST while live -> 409, and it does NOT start a 2nd run.
    h2 = FakeHandler(body={"message": "again"})
    task_server.handle_chat(h2, "TASK-1")
    assert h2.status == 409

    # Release the first run and join.
    started["go"] = True
    t.join(timeout=2)


def test_handle_chat_409_when_agent_running(monkeypatch):
    called = {"run": False}

    def fake_run_turn(task_id, message):
        called["run"] = True
        yield {"kind": "text", "text": "x"}

    monkeypatch.setattr(chat_runner, "run_turn", fake_run_turn)
    monkeypatch.setattr(
        task_lib, "read_task",
        lambda tid: {"frontmatter": {"agent_status": "running"}, "body": ""},
    )

    handler = FakeHandler(body={"message": "hello"})
    task_server.handle_chat(handler, "TASK-1")

    assert handler.status == 409
    assert called["run"] is False
    # No run started when the background agent is busy.
    assert not live_runs.is_live(task_server._chat_run_key("TASK-1"))


def test_handle_chat_404_when_task_missing(monkeypatch):
    def fake_read_task(tid):
        raise FileNotFoundError(tid)

    monkeypatch.setattr(task_lib, "read_task", fake_read_task)

    handler = FakeHandler(body={"message": "hello"})
    task_server.handle_chat(handler, "TASK-9999")

    assert handler.status == 404
    assert not live_runs.is_live(task_server._chat_run_key("TASK-9999"))


def test_handle_chat_streams_events(monkeypatch, tasks_root):
    events = [
        {"kind": "think", "text": "pondering"},
        {"kind": "text", "text": "done"},
    ]

    monkeypatch.setattr(chat_runner, "run_turn", _persisting_run_turn(events))
    monkeypatch.setattr(
        task_lib, "read_task",
        lambda tid: {"frontmatter": {"agent_status": "complete"}, "body": ""},
    )

    handler = FakeHandler(body={"message": "hello"})
    task_server.handle_chat(handler, "TASK-1")

    out = handler.written()
    assert handler.status == 200
    assert handler.sent_headers.get("Content-Type") == "text/event-stream"
    # Two data frames + a terminal done event.
    assert out.count("data: ") >= 2
    assert "pondering" in out
    assert "done" in out
    assert "event: done" in out
    # CONTRACT GUARD for the frontend settle trigger: a clean (non-disconnect)
    # turn's stream must TERMINATE with the `event: done` sentinel. chat.js fires
    # settleDetailFromServer() on this sentinel (the live read tails the durable
    # transcript and never sees the runner's `result` metadata frame), so the
    # backend ending on `event: done` is what makes the left-pane refresh reliable.
    assert out.rstrip().endswith("event: done\ndata: {}")
    # Run finished (drained) — no longer live.
    _wait_done(task_server._chat_run_key("TASK-1"))
    assert not live_runs.is_live(task_server._chat_run_key("TASK-1"))


def test_handle_chat_run_survives_client_disconnect(monkeypatch, tasks_root):
    """THE must-have: a client disconnect (a wfile.write raising BrokenPipeError
    partway) stops only the SSE tail — the underlying run keeps going, finishes,
    and the transcript receives ALL its events."""
    events = [
        {"kind": "text", "text": "first"},
        {"kind": "text", "text": "second"},
        {"kind": "text", "text": "third"},
        {"kind": "result", "session_id": "s"},
    ]
    monkeypatch.setattr(chat_runner, "run_turn", _persisting_run_turn(events))
    monkeypatch.setattr(
        task_lib, "read_task",
        lambda tid: {"frontmatter": {"agent_status": "complete"}, "body": ""},
    )

    # A wfile whose 2nd write raises BrokenPipeError — the SSE tail dies, but the
    # run thread (which never touches wfile) must press on regardless.
    class FlakyWfile:
        def __init__(self):
            self.writes = 0

        def write(self, data):
            self.writes += 1
            if self.writes >= 2:
                raise BrokenPipeError("client gone")

        def flush(self):
            pass

    handler = FakeHandler(body={"message": "hello"})
    handler.wfile = FlakyWfile()

    # Must not raise even though the socket breaks mid-stream.
    task_server.handle_chat(handler, "TASK-1")

    # The run is owned by live_runs, not the dead SSE tail — wait for it to finish.
    key = task_server._chat_run_key("TASK-1")
    _wait_done(key)
    assert not live_runs.is_live(key)
    # And it errored on nothing — clean finish.
    assert live_runs.run_error(key) is None
    # CRUCIAL: every event reached the durable transcript despite the disconnect.
    persisted = chat_transcript.read_events("TASK-1")
    texts = [e.get("text") for e in persisted if e.get("kind") == "text"]
    assert texts == ["first", "second", "third"]


def test_handle_chat_emits_single_cors_header(monkeypatch, tasks_root):
    """The SSE response must carry EXACTLY ONE Access-Control-Allow-Origin
    header — _sse_begin must NOT send it (end_headers injects it)."""

    monkeypatch.setattr(
        chat_runner, "run_turn",
        _persisting_run_turn([{"kind": "text", "text": "hi"}]),
    )
    monkeypatch.setattr(
        task_lib, "read_task",
        lambda tid: {"frontmatter": {"agent_status": "complete"}, "body": ""},
    )

    handler = FakeHandler(body={"message": "hello"})
    task_server.handle_chat(handler, "TASK-1")

    cors = [
        (k, v) for (k, v) in handler.header_list
        if k == "Access-Control-Allow-Origin"
    ]
    assert len(cors) == 1, f"expected exactly one CORS header, got {cors}"
    assert cors[0] == ("Access-Control-Allow-Origin", "*")


# ─── handle_get_chat (history reload) ────────────────────────────────────────────

def test_handle_get_chat_returns_events_in_order(tasks_root):
    """GET history returns the persisted events in append order under {"events":[...]}.

    The transcript path derives from task_lib.TASKS_DIR (redirected by the
    tasks_root fixture), so append_event writes into the temp tree and
    handle_get_chat reads it back.
    """
    task_id, _ = task_lib.create_task(title="Chat me", queue="agent")
    chat_transcript.append_event(task_id, {"role": "user", "kind": "text",
                                           "text": "first", "run_id": "r1"})
    chat_transcript.append_event(task_id, {"role": "assistant", "kind": "text",
                                           "text": "second", "run_id": "r1"})

    handler = FakeHandler()
    task_server.handle_get_chat(handler, task_id)

    assert handler.status == 200
    body = json.loads(handler.written())
    events = body["events"]
    assert len(events) == 2
    assert events[0]["text"] == "first"
    assert events[0]["role"] == "user"
    assert events[1]["text"] == "second"
    assert events[1]["role"] == "assistant"


def test_handle_get_chat_empty_when_no_history(tasks_root):
    """A task with no transcript yields {"events": []} (never 500s the panel)."""
    task_id, _ = task_lib.create_task(title="No chat yet", queue="agent")
    handler = FakeHandler()
    task_server.handle_get_chat(handler, task_id)
    assert handler.status == 200
    assert json.loads(handler.written()) == {"events": []}


def test_handle_get_chat_degrades_on_read_error(monkeypatch):
    """A read failure degrades to {"events": []} rather than raising/500ing."""
    def boom(_tid):
        raise OSError("disk gone")
    monkeypatch.setattr(chat_transcript, "read_events", boom)
    handler = FakeHandler()
    task_server.handle_get_chat(handler, "TASK-0001")
    assert handler.status == 200
    assert json.loads(handler.written()) == {"events": []}


def test_handle_chat_emits_error_frame_on_mid_stream_failure(monkeypatch, tasks_root):
    """A non-disconnect failure in the run (the runner raises after persisting a
    partial event) ends the stream with a terminal error frame sourced from
    live_runs.run_error — and never escapes handle_chat."""

    def boom_run_turn(task_id, message):
        chat_transcript.append_event(task_id, {"kind": "text", "text": "partial"})
        yield {"kind": "text", "text": "partial"}
        raise RuntimeError("boom mid-stream")

    monkeypatch.setattr(chat_runner, "run_turn", boom_run_turn)
    monkeypatch.setattr(
        task_lib, "read_task",
        lambda tid: {"frontmatter": {"agent_status": "complete"}, "body": ""},
    )

    handler = FakeHandler(body={"message": "hello"})

    # (a) Must not raise.
    task_server.handle_chat(handler, "TASK-1")

    out = handler.written()
    # The first (normal) event still made it out.
    assert "partial" in out
    # (b) A terminal error frame was written (after the tail saw run_error).
    assert '"kind": "error"' in out or '"kind":"error"' in out
    # (c) The run is done and recorded the error.
    key = task_server._chat_run_key("TASK-1")
    _wait_done(key)
    assert live_runs.run_error(key) is not None
