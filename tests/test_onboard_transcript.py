"""Tests for scripts/onboard_transcript.py - the single-session onboarding log.

Mirrors test surface of chat/adapt transcripts but single-session (no id key):
append-then-read returns events in order, reset clears, read with no file
returns []. STORE is monkeypatched at a tmp path so the real log is untouched.
"""
import pytest

import onboard_transcript


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(onboard_transcript, "STORE",
                        str(tmp_path / "onboard_transcript.jsonl"))


def test_read_with_no_file_returns_empty():
    assert onboard_transcript.read_events() == []


def test_append_then_read_returns_events_in_order():
    onboard_transcript.append_event({"kind": "text", "text": "first"})
    onboard_transcript.append_event({"kind": "text", "text": "second"})
    events = onboard_transcript.read_events()
    assert [e["text"] for e in events] == ["first", "second"]


def test_append_stamps_ts_when_absent():
    stamped = onboard_transcript.append_event({"kind": "text", "text": "hi"})
    assert "ts" in stamped
    assert onboard_transcript.read_events()[0]["ts"] == stamped["ts"]


def test_reset_clears_the_log():
    onboard_transcript.append_event({"kind": "text", "text": "first"})
    onboard_transcript.reset()
    assert onboard_transcript.read_events() == []


def test_reset_is_safe_when_no_file():
    # No file yet: reset must not raise.
    onboard_transcript.reset()
    assert onboard_transcript.read_events() == []
