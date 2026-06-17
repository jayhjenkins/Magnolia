"""Tests for scripts/sentinel_lib.py — sentinel definition load + validate.

Sentinels are a read-only primitive sibling to workers: they READ sources and
return observations. This task only defines + validates them. The contract is
structural here: kind == sentinel, every source is mode: read, observation_kinds
is a non-empty subset of program_lib.OBSERVATION_KINDS, a non-empty prompt body,
and no write-capable tool grant.
"""
import copy

import program_lib
import sentinel_lib


SHIPPED = ("movement-watch", "tracker-truth")


def test_load_movement_watch():
    d = sentinel_lib.load_sentinel("movement-watch")
    assert d["name"] == "movement-watch"
    assert d["kind"] == "sentinel"
    assert d["sources"], "sentinel must declare sources"
    assert all(s.get("mode") == "read" for s in d["sources"])
    assert d["observation_kinds"]
    assert set(d["observation_kinds"]) <= program_lib.OBSERVATION_KINDS
    assert d["prompt"].strip(), "non-empty prompt body"


def test_load_tracker_truth():
    d = sentinel_lib.load_sentinel("tracker-truth")
    assert d["name"] == "tracker-truth"
    assert d["kind"] == "sentinel"
    assert all(s.get("mode") == "read" for s in d["sources"])
    assert set(d["observation_kinds"]) <= program_lib.OBSERVATION_KINDS
    assert d["prompt"].strip()


def test_list_sentinels_returns_both_shipped():
    names = {d["name"] for d in sentinel_lib.list_sentinels()}
    for n in SHIPPED:
        assert n in names


def test_validate_accepts_shipped_defs():
    for n in SHIPPED:
        d = sentinel_lib.load_sentinel(n)
        assert sentinel_lib.validate_sentinel(d) == []


def _good():
    return {
        "name": "probe",
        "kind": "sentinel",
        "sources": [{"kind": "transcripts", "mode": "read"}],
        "observation_kinds": ["status-signal", "completion"],
        "scope": "active-programs",
        "prompt": "Read the sources and attribute signals to programs.",
    }


def test_validate_rejects_write_source():
    d = _good()
    d["sources"] = [{"kind": "transcripts", "mode": "write"}]
    errs = sentinel_lib.validate_sentinel(d)
    assert any("mode" in e.lower() for e in errs)


def test_validate_rejects_out_of_enum_kind():
    d = _good()
    d["observation_kinds"] = ["status-signal", "vibes"]
    errs = sentinel_lib.validate_sentinel(d)
    assert any("vibes" in e for e in errs)


def test_validate_rejects_empty_observation_kinds():
    d = _good()
    d["observation_kinds"] = []
    errs = sentinel_lib.validate_sentinel(d)
    assert errs


def test_validate_rejects_missing_body():
    d = _good()
    d["prompt"] = "   "
    errs = sentinel_lib.validate_sentinel(d)
    assert any("body" in e.lower() or "prompt" in e.lower() for e in errs)


def test_validate_rejects_wrong_kind():
    d = _good()
    d["kind"] = "worker"
    errs = sentinel_lib.validate_sentinel(d)
    assert any("kind" in e.lower() for e in errs)


def test_validate_rejects_empty_name():
    d = _good()
    d["name"] = ""
    errs = sentinel_lib.validate_sentinel(d)
    assert any("name" in e.lower() for e in errs)


def test_validate_rejects_write_capable_tool():
    for tool in ("Write", "Write(*)", "Edit", "Edit(*)",
                 "MultiEdit", "NotebookEdit", "notebookedit(foo)"):
        d = _good()
        d["allowed_tools"] = ["Read(*)", tool]
        errs = sentinel_lib.validate_sentinel(d)
        assert any("write" in e.lower() or "edit" in e.lower() or "tool" in e.lower()
                   for e in errs), f"{tool} should be rejected"


def test_validate_allows_read_only_tools():
    d = _good()
    d["allowed_tools"] = ["Read(*)", "Bash(*)", "mcp__qmd__*"]
    assert sentinel_lib.validate_sentinel(d) == []


def test_validate_messages_are_ascii():
    d = _good()
    d["observation_kinds"] = ["nope"]
    d["sources"] = [{"kind": "x", "mode": "write"}]
    for e in sentinel_lib.validate_sentinel(d):
        e.encode("ascii")  # raises if non-ascii
