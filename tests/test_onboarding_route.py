"""Tests for the onboarding SSE route + first-run gate in task_server.py.

Mirrors test_chat_route: FakeHandler stands in for a real socket, a canned
onboard_runner.run_turn persists each event to onboard_transcript (the durable
log the handler tails), and live_runs is reset around every test.
"""
import io
import json
import threading
import time

import pytest

import task_server
import onboard_runner
import onboard_transcript
import profile_lib
import live_runs


# ─── Fake handler ──────────────────────────────────────────────────────────────

class FakeHandler:
    def __init__(self, body=None):
        raw = json.dumps(body or {}).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = {}
        self.header_list = []
        self.ended = False

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers[key] = value
        self.header_list.append((key, value))

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.ended = True

    def written(self):
        return self.wfile.getvalue().decode("utf-8")


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_runs():
    live_runs._reset()
    yield
    live_runs._reset()


@pytest.fixture(autouse=True)
def tmp_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard_transcript, "STORE",
                        str(tmp_path / "onboard_transcript.jsonl"))


def _persisting_run_turn(events):
    """A canned onboard_runner.run_turn that appends each event to the durable
    log (the handler tails it), mirroring the real runner."""
    def _gen(message):
        for ev in events:
            onboard_transcript.append_event(dict(ev))
            yield ev
    return _gen


def _wait_done(key, timeout=2.0):
    deadline = time.time() + timeout
    while live_runs.is_live(key) and time.time() < deadline:
        time.sleep(0.01)


# ─── handle_onboarding_run ───────────────────────────────────────────────────

def test_rejects_empty_message():
    called = {"run": False}

    def fake_run_turn(message):
        called["run"] = True
        yield {"kind": "text", "text": "x"}

    handler = FakeHandler(body={"message": "   "})
    task_server.handle_onboarding_run(handler)
    assert handler.status == 400
    assert called["run"] is False


def test_streams_events(monkeypatch):
    events = [
        {"kind": "think", "text": "greeting"},
        {"kind": "text", "text": "welcome in"},
    ]
    monkeypatch.setattr(onboard_runner, "run_turn", _persisting_run_turn(events))

    handler = FakeHandler(body={"message": "onboard me"})
    task_server.handle_onboarding_run(handler)

    out = handler.written()
    assert handler.status == 200
    assert handler.sent_headers.get("Content-Type") == "text/event-stream"
    assert "welcome in" in out
    assert "event: done" in out
    _wait_done(task_server._onboarding_run_key())
    assert not live_runs.is_live(task_server._onboarding_run_key())


def test_concurrent_run_returns_409(monkeypatch):
    key = task_server._onboarding_run_key()
    started = {"go": False}

    def slow_run_turn(message):
        onboard_transcript.append_event({"kind": "text", "text": "first"})
        yield {"kind": "text", "text": "first"}
        while not started["go"]:
            time.sleep(0.01)
        yield {"kind": "result", "session_id": "s"}

    monkeypatch.setattr(onboard_runner, "run_turn", slow_run_turn)

    h1 = FakeHandler(body={"message": "onboard me"})
    t = threading.Thread(target=task_server.handle_onboarding_run, args=(h1,))
    t.start()
    deadline = time.time() + 2
    while not live_runs.is_live(key) and time.time() < deadline:
        time.sleep(0.01)
    assert live_runs.is_live(key)

    h2 = FakeHandler(body={"message": "again"})
    task_server.handle_onboarding_run(h2)
    assert h2.status == 409

    started["go"] = True
    t.join(timeout=2)


# ─── First-run gate ──────────────────────────────────────────────────────────

def test_should_onboard_true_when_not_complete(monkeypatch):
    monkeypatch.setattr(profile_lib, "onboarding_complete", lambda *a, **k: False)
    assert task_server._should_onboard() is True


def test_should_onboard_false_when_complete(monkeypatch):
    monkeypatch.setattr(profile_lib, "onboarding_complete", lambda *a, **k: True)
    assert task_server._should_onboard() is False
