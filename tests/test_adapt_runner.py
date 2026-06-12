"""Tests for scripts/adapt_runner.py - the Adapt gated build session.

The boundaries are mocked: `_spawn` yields canned stream-json lines, the git
helpers are monkeypatched to simulate commits touching specific files, and a
temp STORE_DIR points the real adaptations_lib at a throwaway directory so
manifest capture is exercised for real (producer side) against the same lib the
consumers read.
"""
import json
import os

import pytest

import adapt_runner
import adaptations_lib
import adapt_transcript
import chat_runner


# --- Canned stream-json helpers ----------------------------------------------

def _line(obj):
    return json.dumps(obj)


def _assistant_text(text):
    return _line({"type": "assistant", "message": {"content": [
        {"type": "text", "text": text}]}})


def _assistant_thinking(text):
    return _line({"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": text}]}})


def _assistant_tool(name, inp):
    return _line({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": name, "input": inp}]}})


def _result(session_id="sess-123", usage=None):
    return _line({"type": "result", "session_id": session_id,
                  "usage": usage or {}, "total_cost_usd": 0.01})


def _canned_stream(session_id="sess-123", usage=None):
    return [
        _assistant_thinking("planning the worker"),
        _assistant_tool("Write", {"file_path": "scripts/workers/foo.md"}),
        _assistant_text("Done - built the worker."),
        _result(session_id=session_id, usage=usage),
    ]


# --- Fixtures ----------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the real adaptations_lib + adapt_transcript at a temp store dir."""
    d = tmp_path / "adaptations"
    d.mkdir()
    monkeypatch.setattr(adaptations_lib, "STORE_DIR", str(d))
    monkeypatch.setattr(adapt_transcript, "STORE_DIR", str(d))
    return d


@pytest.fixture
def stub_model(monkeypatch):
    monkeypatch.setattr(adapt_runner.profile_lib, "resolve_model",
                        lambda *a, **k: "claude-test-model")


class _GitSim:
    """A scriptable stand-in for adapt_runner's git helpers.

    `commits` is an ordered list of (sha, [changed_paths]) the post-turn capture
    should see. `registry_added` maps a sha -> list of newly-added cardType keys
    (used to simulate the registry diff).
    """

    def __init__(self, head_before="HEAD0", commits=None, registry_added=None):
        self.head_before = head_before
        self.commits = commits or []
        self.registry_added = registry_added or {}

    def rev_parse_head(self):
        # Before the turn returns head_before; after, the newest commit (or
        # head_before if nothing was committed).
        return self.commits[-1][0] if self.commits else self.head_before

    def rev_list(self, prev):
        # Oldest -> newest order of new commit shas.
        return [sha for sha, _ in self.commits]

    def changed_paths(self, sha):
        for s, paths in self.commits:
            if s == sha:
                return list(paths)
        return []

    def registry_added_keys(self, sha):
        return list(self.registry_added.get(sha, []))


def _install_git(monkeypatch, sim, head_before="HEAD0"):
    """Wire a _GitSim into adapt_runner's git seams."""
    state = {"phase": "before"}

    def fake_rev_parse_head():
        if state["phase"] == "before":
            return head_before
        return sim.rev_parse_head()

    monkeypatch.setattr(adapt_runner, "_git_head", fake_rev_parse_head)
    monkeypatch.setattr(adapt_runner, "_git_new_commits",
                        lambda prev: sim.rev_list(prev))
    monkeypatch.setattr(adapt_runner, "_git_changed_paths",
                        lambda sha: sim.changed_paths(sha))
    monkeypatch.setattr(adapt_runner, "_git_registry_added_keys",
                        lambda sha: sim.registry_added_keys(sha))
    return state


def _run(monkeypatch, adaptation_id, message, stream, sim, head_before="HEAD0"):
    """Drive run_turn with a canned stream + git sim; return yielded events."""
    state = _install_git(monkeypatch, sim, head_before=head_before)

    def fake_spawn(cmd, exit_holder=None):
        # Flip the git phase the moment the stream is consumed (turn ran).
        state["phase"] = "after"
        for ln in stream:
            yield ln
        if exit_holder is not None:
            exit_holder["returncode"] = 0

    monkeypatch.setattr(adapt_runner, "_spawn", fake_spawn)
    return list(adapt_runner.run_turn(adaptation_id, message))


# --- build_chat_cmd extension stays backward compatible ----------------------

def test_build_chat_cmd_still_prompt_first_allowedtools_last():
    cmd = chat_runner.build_chat_cmd("S1", "hello", "m", new_session=True)
    assert cmd[1] == "hello"
    assert cmd[-2] == "--allowedTools"


def test_build_chat_cmd_accepts_append_system_prompt_and_settings():
    cmd = chat_runner.build_chat_cmd(
        "S1", "hello", "m", new_session=False,
        append_system_prompt="HARNESS", settings="/abs/adapt_settings.json")
    assert cmd[1] == "hello"                       # prompt still first
    assert cmd[-2] == "--allowedTools"             # allowedTools still last
    assert "--append-system-prompt" in cmd
    assert cmd[cmd.index("--append-system-prompt") + 1] == "HARNESS"
    assert "--settings" in cmd
    assert cmd[cmd.index("--settings") + 1] == "/abs/adapt_settings.json"


# --- New build: row creation, provisional name, adaptation event -------------

def test_new_build_creates_row_on_first_result_session_id(store, stub_model, monkeypatch):
    sim = _GitSim(commits=[])  # no commits this turn
    events = _run(monkeypatch, None, "Build a churn-risk worker that flags accounts",
                  _canned_stream(session_id="sess-abc"), sim)

    # The adaptation event was yielded with the new id + provisional name.
    adapt_evts = [e for e in events if e.get("kind") == "adaptation"]
    assert len(adapt_evts) == 1
    new_id = adapt_evts[0]["adaptation_id"]
    assert adapt_evts[0]["state"] == "building"
    assert adapt_evts[0]["name"].startswith("Build a churn-risk worker")

    rec = adaptations_lib.read(new_id)
    assert rec["state"] == "building"               # no artifacts -> stays building
    assert rec["claude_session_id"] == "sess-abc"
    assert rec["name"].startswith("Build a churn-risk worker")


def test_new_build_creates_row_with_minted_id_when_result_has_no_session_id(
        store, stub_model, monkeypatch):
    """A `result` with NO session_id must still create the row, falling back to
    the minted --session-id sid. The buffered pre-id events still flush to the
    log. Mirrors chat_runner.run_turn's `result_sid or sid` fallback - without
    it the row is never created and the buffered events + manifest are lost."""
    captured = {}

    def fake_spawn(cmd, exit_holder=None):
        captured["cmd"] = cmd
        # A result event with NO session_id key at all.
        for ln in [
            _assistant_thinking("planning the worker"),
            _assistant_tool("Write", {"file_path": "scripts/workers/foo.md"}),
            _assistant_text("Done - built the worker."),
            _line({"type": "result", "usage": {}, "total_cost_usd": 0.01}),
        ]:
            yield ln
        if exit_holder is not None:
            exit_holder["returncode"] = 0

    sim = _GitSim(commits=[])
    state = _install_git(monkeypatch, sim, head_before="HEAD0")

    def wrapped(cmd, exit_holder=None):
        state["phase"] = "after"
        yield from fake_spawn(cmd, exit_holder)

    monkeypatch.setattr(adapt_runner, "_spawn", wrapped)
    events = list(adapt_runner.run_turn(None, "Build the foo worker"))

    # The minted session id (from --session-id) is what the row should use.
    cmd = captured["cmd"]
    minted_sid = cmd[cmd.index("--session-id") + 1]

    adapt_evts = [e for e in events if e.get("kind") == "adaptation"]
    assert len(adapt_evts) == 1
    new_id = adapt_evts[0]["adaptation_id"]

    rec = adaptations_lib.read(new_id)
    assert rec["claude_session_id"] == minted_sid

    # The buffered pre-id events still flushed to the durable log.
    logged = adapt_transcript.read_events(new_id)
    kinds = [e.get("kind") for e in logged]
    assert "think" in kinds
    assert "tool_step" in kinds
    assert "text" in kinds
    assert "adaptation" in kinds


def test_state_flip_building_to_off_yields_second_adaptation_event(
        store, stub_model, monkeypatch):
    """When the manifest grows and state flips building -> off, a SECOND
    adaptation event (state: "off") must be yielded so a live UI client can
    update the rail dot/toggle without re-reading the record. The flip event is
    persisted to the log too (for reconnect/replay)."""
    sim = _GitSim(commits=[("sha-worker", ["scripts/workers/foo.md"])])
    events = _run(monkeypatch, None, "Build the foo worker",
                  _canned_stream(session_id="sess-flip"), sim)

    adapt_evts = [e for e in events if e.get("kind") == "adaptation"]
    assert len(adapt_evts) == 2
    assert adapt_evts[0]["state"] == "building"
    assert adapt_evts[1]["state"] == "off"
    # Both events share the same adaptation id.
    assert adapt_evts[0]["adaptation_id"] == adapt_evts[1]["adaptation_id"]
    new_id = adapt_evts[0]["adaptation_id"]

    # The flip event is in the durable log too.
    logged = adapt_transcript.read_events(new_id)
    off_evts = [e for e in logged
                if e.get("kind") == "adaptation" and e.get("state") == "off"]
    assert len(off_evts) == 1


def test_provisional_name_capped_and_fallback(store, stub_model, monkeypatch):
    sim = _GitSim(commits=[])
    long_msg = "x" * 200
    events = _run(monkeypatch, None, long_msg, _canned_stream(), sim)
    name = [e for e in events if e.get("kind") == "adaptation"][0]["name"]
    assert len(name) <= 48

    sim2 = _GitSim(commits=[])
    events2 = _run(monkeypatch, None, "   ", _canned_stream(session_id="sess-z"), sim2)
    name2 = [e for e in events2 if e.get("kind") == "adaptation"][0]["name"]
    assert name2 == "New adaptation"


# --- Manifest capture: worker (ref convention pin) ---------------------------

def test_worker_commit_adds_manifest_entry_and_flips_state_off(store, stub_model, monkeypatch):
    sim = _GitSim(commits=[("sha-worker", ["scripts/workers/foo.md"])])
    events = _run(monkeypatch, None, "Build the foo worker",
                  _canned_stream(session_id="sess-w"), sim)
    new_id = [e for e in events if e.get("kind") == "adaptation"][0]["adaptation_id"]

    rec = adaptations_lib.read(new_id)
    worker_entries = [m for m in rec["manifest"] if m["surface"] == "worker"]
    assert len(worker_entries) == 1
    assert worker_entries[0]["ref"] == "scripts/workers/foo.md"
    assert worker_entries[0]["commit"] == "sha-worker"
    # Manifest grew -> state flips building -> off.
    assert rec["state"] == "off"


def test_worker_ref_matches_load_workers_consumer_expression(store, stub_model, monkeypatch):
    """Producer-ref == consumer-ref pin: the stored worker ref must equal the
    exact expression task_dispatch.load_workers uses (os.path.relpath against
    PM_OS_DIR)."""
    worker_path = os.path.join(adapt_runner.PM_OS_DIR, "scripts", "workers", "foo.md")
    sim = _GitSim(commits=[("sha1", ["scripts/workers/foo.md"])])
    events = _run(monkeypatch, None, "Build foo", _canned_stream(), sim)
    new_id = [e for e in events if e.get("kind") == "adaptation"][0]["adaptation_id"]

    rec = adaptations_lib.read(new_id)
    stored_ref = [m for m in rec["manifest"] if m["surface"] == "worker"][0]["ref"]
    assert stored_ref == os.path.relpath(worker_path, adapt_runner.PM_OS_DIR)


# --- Manifest capture: adapter -----------------------------------------------

def test_adapter_commit_adds_entry_with_family_provider_ref(store, stub_model, monkeypatch):
    sim = _GitSim(commits=[("sha-a", ["scripts/adapters/ecommerce/shopify.py"])])
    events = _run(monkeypatch, None, "Build a shopify adapter", _canned_stream(), sim)
    new_id = [e for e in events if e.get("kind") == "adaptation"][0]["adaptation_id"]

    rec = adaptations_lib.read(new_id)
    adapter_entries = [m for m in rec["manifest"] if m["surface"] == "adapter"]
    assert len(adapter_entries) == 1
    assert adapter_entries[0]["ref"] == "ecommerce/shopify"


def test_adapter_skips_contract_and_init(store, stub_model, monkeypatch):
    sim = _GitSim(commits=[("sha-a", [
        "scripts/adapters/ecommerce/__init__.py",
        "scripts/adapters/ecommerce/_contract.py",
    ])])
    events = _run(monkeypatch, None, "touch adapter plumbing", _canned_stream(), sim)
    new_id = [e for e in events if e.get("kind") == "adaptation"][0]["adaptation_id"]
    rec = adaptations_lib.read(new_id)
    assert not [m for m in rec["manifest"] if m["surface"] == "adapter"]


# --- Manifest capture: card-type ---------------------------------------------

def test_registry_commit_adds_card_type_for_each_new_key(store, stub_model, monkeypatch):
    sim = _GitSim(
        commits=[("sha-c", ["ui/task-board/cardtypes/registry.json"])],
        registry_added={"sha-c": ["churn_alert"]},
    )
    events = _run(monkeypatch, None, "Add a churn alert card", _canned_stream(), sim)
    new_id = [e for e in events if e.get("kind") == "adaptation"][0]["adaptation_id"]

    rec = adaptations_lib.read(new_id)
    ct = [m for m in rec["manifest"] if m["surface"] == "card-type"]
    assert len(ct) == 1
    assert ct[0]["ref"] == "churn_alert"


# --- Registry-key parse/diff helper (pure, no git) ---------------------------

def test_registry_added_from_pair_returns_only_new_keys():
    """Given before {task, receipt} and after {task, receipt, stock-alert}, only
    stock-alert is newly added. Tests the parse/diff helper directly on JSON
    strings - no git."""
    before = json.dumps({"cardTypes": {"task": {}, "receipt": {}}})
    after = json.dumps({"cardTypes": {"task": {}, "receipt": {}, "stock-alert": {}}})
    assert adapt_runner._registry_added_from_pair(before, after) == ["stock-alert"]


def test_registry_added_from_pair_identical_is_empty():
    """Identical before/after -> no newly-added keys."""
    same = json.dumps({"cardTypes": {"task": {}, "receipt": {}}})
    assert adapt_runner._registry_added_from_pair(same, same) == []


# --- Resume path argv --------------------------------------------------------

def test_resume_argv_has_resume_harness_settings_allowedtools(store, stub_model, monkeypatch):
    rid = adaptations_lib.create("Existing build", "stored-sid-xyz")
    captured = {}

    def fake_spawn(cmd, exit_holder=None):
        captured["cmd"] = cmd
        for ln in _canned_stream(session_id="stored-sid-xyz"):
            yield ln
        if exit_holder is not None:
            exit_holder["returncode"] = 0

    monkeypatch.setattr(adapt_runner, "_spawn", fake_spawn)
    monkeypatch.setattr(adapt_runner, "_git_head", lambda: "HEAD0")
    monkeypatch.setattr(adapt_runner, "_git_new_commits", lambda prev: [])
    monkeypatch.setattr(adapt_runner, "_git_changed_paths", lambda sha: [])
    monkeypatch.setattr(adapt_runner, "_git_registry_added_keys", lambda sha: [])

    list(adapt_runner.run_turn(rid, "keep going"))
    cmd = captured["cmd"]
    assert "--resume" in cmd and cmd[cmd.index("--resume") + 1] == "stored-sid-xyz"
    assert "--append-system-prompt" in cmd
    assert "--settings" in cmd
    settings_path = cmd[cmd.index("--settings") + 1]
    assert settings_path.endswith(os.path.join("scripts", "hooks", "adapt_settings.json"))
    assert os.path.isabs(settings_path)
    assert "--allowedTools" in cmd


def test_new_build_argv_uses_session_id_and_adapt_tools(store, stub_model, monkeypatch):
    captured = {}

    def fake_spawn(cmd, exit_holder=None):
        captured["cmd"] = cmd
        for ln in _canned_stream():
            yield ln

    monkeypatch.setattr(adapt_runner, "_spawn", fake_spawn)
    monkeypatch.setattr(adapt_runner, "_git_head", lambda: "HEAD0")
    monkeypatch.setattr(adapt_runner, "_git_new_commits", lambda prev: [])
    monkeypatch.setattr(adapt_runner, "_git_changed_paths", lambda sha: [])
    monkeypatch.setattr(adapt_runner, "_git_registry_added_keys", lambda sha: [])

    list(adapt_runner.run_turn(None, "build something"))
    cmd = captured["cmd"]
    assert "--session-id" in cmd
    tools = cmd[cmd.index("--allowedTools") + 1]
    # ADAPT_ALLOWED_TOOLS, comma-joined.
    assert "Write(scripts/workers/**)" in tools


# --- Compaction --------------------------------------------------------------

def test_compaction_notice_when_window_full(store, stub_model, monkeypatch):
    # input_tokens way over half of the 200k default window.
    usage = {"input_tokens": 150000}
    sim = _GitSim(commits=[])
    events = _run(monkeypatch, None, "big build",
                  _canned_stream(usage=usage), sim)
    notices = [e for e in events if e.get("kind") == "notice"]
    assert any("compact" in (n.get("text") or "").lower() for n in notices)


def test_no_compaction_notice_when_window_small(store, stub_model, monkeypatch):
    usage = {"input_tokens": 10}
    sim = _GitSim(commits=[])
    events = _run(monkeypatch, None, "small build", _canned_stream(usage=usage), sim)
    assert not [e for e in events if e.get("kind") == "notice"]


def test_post_ship_enqueues_one_compact_turn_best_effort(store, stub_model, monkeypatch):
    """When the turn ships (manifest grows), exactly one follow-up /compact turn
    is enqueued against the same session - housekeeping, failures swallowed."""
    spawn_calls = []

    def fake_spawn(cmd, exit_holder=None):
        spawn_calls.append(cmd)
        if len(spawn_calls) == 1:
            for ln in _canned_stream(session_id="sess-ship"):
                yield ln
        else:
            # The /compact housekeeping turn - blow up to prove it's swallowed.
            raise RuntimeError("compact turn failed")

    state = {"phase": "before"}
    sim = _GitSim(commits=[("sha-w", ["scripts/workers/foo.md"])])

    def fake_head():
        return "HEAD0" if state["phase"] == "before" else sim.rev_parse_head()

    monkeypatch.setattr(adapt_runner, "_spawn", fake_spawn)
    monkeypatch.setattr(adapt_runner, "_git_head", fake_head)

    def commits_then_flip(prev):
        return sim.rev_list(prev)

    monkeypatch.setattr(adapt_runner, "_git_new_commits", commits_then_flip)
    monkeypatch.setattr(adapt_runner, "_git_changed_paths",
                        lambda sha: sim.changed_paths(sha))
    monkeypatch.setattr(adapt_runner, "_git_registry_added_keys", lambda sha: [])

    # Flip phase when the first stream is consumed.
    orig = fake_spawn

    def wrapped(cmd, exit_holder=None):
        state["phase"] = "after"
        yield from orig(cmd, exit_holder)

    monkeypatch.setattr(adapt_runner, "_spawn", wrapped)

    events = list(adapt_runner.run_turn(None, "build foo worker"))
    # The build itself succeeded and was captured despite the compact failure.
    new_id = [e for e in events if e.get("kind") == "adaptation"][0]["adaptation_id"]
    rec = adaptations_lib.read(new_id)
    assert rec["state"] == "off"
    # A second (housekeeping) spawn was attempted.
    assert len(spawn_calls) == 2
    # The compact turn's RuntimeError did not bubble out (no events from it).


# --- Persistence: event log --------------------------------------------------

def test_yielded_events_are_appended_to_event_log(store, stub_model, monkeypatch):
    sim = _GitSim(commits=[])
    events = _run(monkeypatch, None, "build a thing",
                  _canned_stream(session_id="sess-log"), sim)
    new_id = [e for e in events if e.get("kind") == "adaptation"][0]["adaptation_id"]

    logged = adapt_transcript.read_events(new_id)
    kinds = [e.get("kind") for e in logged]
    # The user-visible stream events made it into the log.
    assert "think" in kinds
    assert "tool_step" in kinds
    assert "text" in kinds
    # The adaptation event (pre-id, buffered then flushed) is in the log too.
    assert "adaptation" in kinds


def test_event_log_roundtrips_text(store, stub_model, monkeypatch):
    sim = _GitSim(commits=[])
    events = _run(monkeypatch, None, "build", _canned_stream(), sim)
    new_id = [e for e in events if e.get("kind") == "adaptation"][0]["adaptation_id"]
    logged = adapt_transcript.read_events(new_id)
    texts = [e.get("text") for e in logged if e.get("kind") == "text"]
    assert any("Done - built the worker." in (t or "") for t in texts)
