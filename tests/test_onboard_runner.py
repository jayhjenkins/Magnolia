"""Tests for scripts/onboard_runner.py - the headless onboarding session.

Mirrors test_adapt_runner: the boundary is mocked (`_spawn` yields canned
stream-json lines), the transcript + session-state are pointed at a tmp dir, and
no real `claude` is ever spawned.

What is asserted:
  - a normal text turn persists each event + yields it;
  - the broad ONBOARD_ALLOWED_TOOLS allowlist and the --settings fairway path
    are passed to build_chat_cmd;
  - a stream containing the ONBOARDING_COMPLETE sentinel yields a synthetic
    `onboarding_complete` event (and persists it);
  - the session id is persisted so a second turn --resumes it.
"""
import json
import os

import pytest

import onboard_runner
import onboard_transcript


# --- Canned stream-json helpers ----------------------------------------------

def _line(obj):
    return json.dumps(obj)


def _assistant_text(text):
    return _line({"type": "assistant", "message": {"content": [
        {"type": "text", "text": text}]}})


def _assistant_thinking(text):
    return _line({"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": text}]}})


def _result(session_id="onb-sess", usage=None):
    return _line({"type": "result", "session_id": session_id,
                  "usage": usage or {}, "total_cost_usd": 0.01})


def _canned_stream(session_id="onb-sess"):
    return [
        _assistant_thinking("greeting the user"),
        _assistant_text("Well hey - welcome in. Let's get you set up."),
        _result(session_id=session_id),
    ]


# --- Fixtures ----------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    """Point the transcript + session-state at a throwaway dir."""
    monkeypatch.setattr(onboard_transcript, "STORE",
                        str(tmp_path / "onboard_transcript.jsonl"))
    monkeypatch.setattr(onboard_runner, "SESSION_STATE_PATH",
                        str(tmp_path / "onboard_session.json"))


@pytest.fixture
def stub_model(monkeypatch):
    monkeypatch.setattr(onboard_runner.profile_lib, "resolve_model",
                        lambda *a, **k: "claude-test-model")


def _run(monkeypatch, message, stream, capture=None):
    """Drive run_turn with a canned stream; return yielded events."""
    def fake_spawn(cmd, exit_holder=None):
        if capture is not None:
            capture["cmd"] = cmd
        for ln in stream:
            yield ln
        if exit_holder is not None:
            exit_holder["returncode"] = 0

    monkeypatch.setattr(onboard_runner, "_spawn", fake_spawn)
    return list(onboard_runner.run_turn(message))


# --- A normal turn persists + yields ----------------------------------------

def test_normal_turn_persists_and_yields(stub_model, monkeypatch):
    events = _run(monkeypatch, "onboard me", _canned_stream())
    kinds = [e.get("kind") for e in events]
    assert "think" in kinds
    assert "text" in kinds
    assert "result" in kinds

    logged = onboard_transcript.read_events()
    logged_kinds = [e.get("kind") for e in logged]
    # think + text persisted; result is metadata (yield-only, not logged).
    assert "think" in logged_kinds
    assert "text" in logged_kinds
    assert "result" not in logged_kinds


# --- The broad allowlist + fairway settings are passed ----------------------

def test_argv_uses_broad_allowlist_and_settings(stub_model, monkeypatch):
    capture = {}
    _run(monkeypatch, "onboard me", _canned_stream(), capture=capture)
    cmd = capture["cmd"]

    assert cmd[1] == "onboard me"            # prompt first
    assert cmd[-2] == "--allowedTools"       # allowedTools last
    tools = cmd[-1]
    # BROAD: Bash, MCP wildcard, Skill, Write/Edit - the opposite of CHAT.
    assert "Bash" in tools
    assert "mcp__claude_ai_*" in tools
    assert "Skill" in tools
    assert "Write" in tools

    assert "--append-system-prompt" in cmd   # the harness is re-injected
    assert "--settings" in cmd
    settings_path = cmd[cmd.index("--settings") + 1]
    assert settings_path.endswith(os.path.join("scripts", "hooks", "onboard_settings.json"))
    assert os.path.isabs(settings_path)


# --- Harness one-direction rule ---------------------------------------------

def test_harness_forbids_board_back_and_forth():
    # The board is revealed once, at the end, by ONBOARDING_COMPLETE - never opened
    # mid-flow with a "go look and come back" beat. The harness must say so.
    h = onboard_runner.build_harness_prompt()
    assert "one direction" in h.lower()
    assert "going back and forth" in h.lower()


# --- Completion sentinel -----------------------------------------------------

def test_sentinel_yields_onboarding_complete_event(stub_model, monkeypatch):
    stream = [
        _assistant_text("All set! ONBOARDING_COMPLETE"),
        _result(session_id="onb-done"),
    ]
    events = _run(monkeypatch, "finish up", stream)
    complete = [e for e in events if e.get("kind") == "onboarding_complete"]
    assert len(complete) == 1
    assert complete[0]["role"] == "system"

    # Persisted to the log for replay.
    logged = onboard_transcript.read_events()
    assert any(e.get("kind") == "onboarding_complete" for e in logged)


def test_no_sentinel_no_complete_event(stub_model, monkeypatch):
    events = _run(monkeypatch, "onboard me", _canned_stream())
    assert [e for e in events if e.get("kind") == "onboarding_complete"] == []


def test_sentinel_only_text_is_stripped_not_yielded(stub_model, monkeypatch):
    # The final text event is EXACTLY the sentinel on its own line. No text event
    # carrying the literal sentinel may be yielded or persisted, but the single
    # synthetic onboarding_complete event must still fire.
    stream = [
        _assistant_text("ONBOARDING_COMPLETE"),
        _result(session_id="onb-done"),
    ]
    events = _run(monkeypatch, "finish up", stream)

    # No yielded text event carries the raw sentinel.
    text_events = [e for e in events if e.get("kind") == "text"]
    assert all(onboard_runner.COMPLETE_SENTINEL not in (e.get("text") or "")
               for e in text_events)
    # The sentinel-only event carried no other prose -> not yielded at all.
    assert text_events == []

    # Exactly one synthetic completion event still fires.
    complete = [e for e in events if e.get("kind") == "onboarding_complete"]
    assert len(complete) == 1

    # Nothing carrying the raw sentinel was persisted either.
    logged = onboard_transcript.read_events()
    assert all(onboard_runner.COMPLETE_SENTINEL not in (e.get("text") or "")
               for e in logged if e.get("kind") == "text")
    assert any(e.get("kind") == "onboarding_complete" for e in logged)


def test_sentinel_appended_to_prose_strips_sentinel_keeps_prose(stub_model, monkeypatch):
    # The sentinel rides along with real prose. The yielded/persisted text event
    # keeps the prose but NOT the literal sentinel, and completion still fires once.
    stream = [
        _assistant_text("You're all set.\nONBOARDING_COMPLETE"),
        _result(session_id="onb-done"),
    ]
    events = _run(monkeypatch, "finish up", stream)

    text_events = [e for e in events if e.get("kind") == "text"]
    assert len(text_events) == 1
    assert "You're all set." in text_events[0]["text"]
    assert onboard_runner.COMPLETE_SENTINEL not in text_events[0]["text"]

    complete = [e for e in events if e.get("kind") == "onboarding_complete"]
    assert len(complete) == 1

    logged = onboard_transcript.read_events()
    logged_text = [e for e in logged if e.get("kind") == "text"]
    assert len(logged_text) == 1
    assert "You're all set." in logged_text[0]["text"]
    assert onboard_runner.COMPLETE_SENTINEL not in logged_text[0]["text"]


# --- Session resume ----------------------------------------------------------

def test_first_turn_is_new_session_and_persists_id(stub_model, monkeypatch):
    capture = {}
    _run(monkeypatch, "onboard me", _canned_stream(session_id="onb-1"), capture=capture)
    cmd = capture["cmd"]
    assert "--session-id" in cmd          # NEW session
    assert "--resume" not in cmd
    # The id was persisted for the next turn.
    assert onboard_runner._read_session_id() == "onb-1"


def test_second_turn_resumes_the_session(stub_model, monkeypatch):
    _run(monkeypatch, "onboard me", _canned_stream(session_id="onb-1"))
    capture = {}
    _run(monkeypatch, "keep going", _canned_stream(session_id="onb-1"), capture=capture)
    cmd = capture["cmd"]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "onb-1"
    assert "--session-id" not in cmd


# --- Transcript + session reset on re-run ------------------------------------

def test_new_run_resets_stale_transcript(stub_model, monkeypatch):
    # A prior run left a stale event (incl. a stale completion) in the log, but
    # NO session state exists -> this is a genuinely new run. It must start from a
    # clean log: the stale event is gone, only this run's events remain.
    onboard_transcript.append_event({"kind": "onboarding_complete",
                                     "role": "system", "text": "stale"})
    assert onboard_transcript.read_events()    # stale event present

    _run(monkeypatch, "onboard me", _canned_stream())

    logged = onboard_transcript.read_events()
    assert not any(e.get("text") == "stale" for e in logged)
    assert any(e.get("kind") == "text" for e in logged)


def test_resuming_run_does_not_reset_transcript(stub_model, monkeypatch):
    # First turn mints + persists a session id; its events land in the log.
    _run(monkeypatch, "onboard me", _canned_stream(session_id="onb-1"))
    first_count = len(onboard_transcript.read_events())
    assert first_count > 0
    # Second turn RESUMES (session id present) -> must NOT reset; events accrue.
    _run(monkeypatch, "keep going", _canned_stream(session_id="onb-1"))
    assert len(onboard_transcript.read_events()) > first_count


def test_completion_clears_session_state(stub_model, monkeypatch):
    stream = [
        _assistant_text("All set! ONBOARDING_COMPLETE"),
        _result(session_id="onb-done"),
    ]
    _run(monkeypatch, "finish up", stream)
    # The completed/dead session must be cleared so the NEXT onboarding mints a
    # fresh session id rather than resuming the dead one.
    assert not os.path.exists(onboard_runner.SESSION_STATE_PATH)
    assert onboard_runner._read_session_id() is None
