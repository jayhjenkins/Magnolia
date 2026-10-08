import json, os, sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import granola_sync


def _profile(tmp_path, provider="granola"):
    (tmp_path / "profile").mkdir(exist_ok=True)
    (tmp_path / "profile" / "integrations.yaml").write_text(
        f"transcript:\n  provider: {provider}\n  target: datasets/meetings/\n")
    (tmp_path / "profile" / "config.yaml").write_text("models: {}\n")


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


NOW = datetime.now(timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Granola profile + isolated state dir; downstream stubbed (records calls)."""
    _profile(tmp_path)
    monkeypatch.setattr(granola_sync.profile_lib, "PM_OS_DIR", str(tmp_path))
    monkeypatch.setattr(granola_sync, "_state_dir", lambda root=None: str(tmp_path / "st"))
    fired = []
    monkeypatch.setattr(granola_sync.transcript_post, "run_downstream",
        lambda txt, mid, state, log: fired.append((mid, str(txt))) or str(txt))
    calls = {"list": [], "fetch": []}

    def use(listed, transcripts):
        def _list(since, root=None):
            calls["list"].append(since)
            if isinstance(listed, Exception):
                raise listed
            return [dict(m) for m in listed]

        def _fetch(batch, root=None):
            calls["fetch"].append([m["id"] for m in batch])
            return {m["id"]: transcripts[m["id"]] for m in batch if m["id"] in transcripts}
        monkeypatch.setattr(granola_sync, "_list_meetings", _list)
        monkeypatch.setattr(granola_sync, "_fetch_transcripts", _fetch)
    return {"root": str(tmp_path), "st": tmp_path / "st", "fired": fired,
            "calls": calls, "use": use}


def _ledger(env):
    return json.loads((env["st"] / "granola_downloaded.json").read_text())


def _status(env):
    return json.loads((env["st"] / "granola_status.json").read_text())


def _m(mid, hours_ago=1, title="Sync", attendees=None):
    return {"id": mid, "title": title, "created_at": _iso(NOW - timedelta(hours=hours_ago)),
            "attendees": attendees or []}


# ── core flow ───────────────────────────────────────────────────────────────

def test_main_writes_and_records(env):
    env["use"]([_m("uuid-9", title="Voice AI call", attendees=["Ann"])],
               {"uuid-9": "Ann: hello world"})
    res = granola_sync.main(root=env["root"])
    assert res["status"] == "ok" and res["new"] == 1
    assert "uuid-9" in _ledger(env)
    assert env["fired"][0][0] == "uuid-9"
    txt = open(env["fired"][0][1], encoding="utf-8").read()
    assert "Attendees: Ann" in txt and "Ann: hello world" in txt
    # second run: already in the ledger -> not fetched again
    env["calls"]["fetch"].clear()
    granola_sync.main(root=env["root"])
    assert env["calls"]["fetch"] == []


def test_main_noop_when_provider_not_granola(tmp_path, monkeypatch):
    _profile(tmp_path, provider="otter")
    called = {"list": False}
    monkeypatch.setattr(granola_sync, "_list_meetings",
        lambda since, root=None: called.__setitem__("list", True) or [])
    result = granola_sync.main(root=str(tmp_path))
    assert called["list"] is False
    assert result["status"] == "skipped"


def test_main_same_minute_title_different_ids_no_overwrite(env):
    m1 = _m("id-aaaa1111", title="Standup"); m2 = dict(m1, id="id-bbbb2222")
    env["use"]([m1, m2], {"id-aaaa1111": "a", "id-bbbb2222": "b"})
    granola_sync.main(root=env["root"])
    paths = [p for _, p in env["fired"]]
    assert len(paths) == 2 and len(set(paths)) == 2
    assert all(os.path.exists(p) for p in paths)


def test_main_downstream_error_isolated(env, monkeypatch):
    env["use"]([_m("bad-1", 3), _m("good-2", 2)], {"bad-1": "x", "good-2": "y"})

    def _downstream(txt, mid, state, log):
        if mid == "bad-1":
            raise RuntimeError("downstream blew up")
        return str(txt)
    monkeypatch.setattr(granola_sync.transcript_post, "run_downstream", _downstream)
    result = granola_sync.main(root=env["root"])
    assert result["new"] == 1
    led = _ledger(env)
    assert "bad-1" in led                         # stays seen (file was written)
    assert led["good-2"].get("final_path")


# ── lookback window (first sync = 3 days; --days override) ─────────────────

def test_first_sync_looks_back_three_days(env):
    env["use"]([_m("recent", 24), _m("old", 24 * 5)], {"recent": "r", "old": "o"})
    granola_sync.main(root=env["root"])
    since = env["calls"]["list"][0]
    assert timedelta(days=2, hours=23) < NOW - since < timedelta(days=3, hours=1)
    assert [m for m, _ in env["fired"]] == ["recent"]   # 5-day-old meeting filtered


def test_days_override(env):
    env["use"]([_m("old", 24 * 5)], {"old": "o"})
    granola_sync.main(root=env["root"], days=7)
    since = env["calls"]["list"][0]
    assert timedelta(days=6, hours=23) < NOW - since < timedelta(days=7, hours=1)
    assert [m for m, _ in env["fired"]] == ["old"]


def test_cli_days_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(granola_sync, "main", lambda root=None, days=None: seen.setdefault("d", days))
    granola_sync.cli(["--days", "10"])
    assert seen["d"] == 10


def test_subsequent_sync_resumes_from_last_success(env):
    env["st"].mkdir(parents=True)
    last = NOW - timedelta(days=10)
    (env["st"] / "granola_status.json").write_text(json.dumps({"last_success": _iso(last)}))
    env["use"]([], {})
    granola_sync.main(root=env["root"])
    since = env["calls"]["list"][0]
    # resumes from last success (with an overlap), not the 3-day first-sync window
    assert since <= last and NOW - since < timedelta(days=12)


# ── empty run still records success (doctor goes green) ─────────────────────

def test_empty_first_run_saves_status_and_ledger(env):
    env["use"]([], {})
    res = granola_sync.main(root=env["root"])
    assert res["status"] == "ok" and res["new"] == 0
    st = _status(env)
    assert st["last_success"] and st.get("last_error") is None
    assert (env["st"] / "granola_downloaded.json").exists()


def test_list_failure_records_error_and_keeps_last_success(env):
    env["st"].mkdir(parents=True)
    prior = _iso(NOW - timedelta(hours=5))
    (env["st"] / "granola_status.json").write_text(json.dumps({"last_success": prior}))
    env["use"](granola_sync.GranolaError("needs_reauth", "Granola tools unavailable"), {})
    res = granola_sync.main(root=env["root"])
    assert res["status"] == "error"
    st = _status(env)
    assert st["last_success"] == prior
    assert st["error_kind"] == "needs_reauth" and "unavailable" in st["last_error"]


# ── batching + per-meeting failure isolation + retry ────────────────────────

def test_transcripts_fetched_in_small_batches(env):
    ids = [f"m{i}" for i in range(5)]
    env["use"]([_m(i, 1 + n) for n, i in enumerate(ids)], {i: "t " + i for i in ids})
    granola_sync.main(root=env["root"])
    assert all(1 <= len(b) <= granola_sync.BATCH_SIZE <= 3 for b in env["calls"]["fetch"])
    assert sorted(sum(env["calls"]["fetch"], [])) == ids
    assert len(_ledger(env)) == 5


def test_failed_meeting_is_pending_then_retried(env, monkeypatch):
    env["use"]([_m("ok-1", 2), _m("flaky-2", 1)], {"ok-1": "fine"})
    res = granola_sync.main(root=env["root"])
    assert res["new"] == 1 and res["failed"] == 1
    assert "ok-1" in _ledger(env) and "flaky-2" not in _ledger(env)
    pend = _status(env)["pending"]
    assert pend["flaky-2"]["attempts"] == 1
    assert _status(env)["last_success"]                 # run itself succeeded
    # next run: listing no longer returns it, but the pending retry still fetches it
    env["calls"]["fetch"].clear()
    env["use"]([], {"flaky-2": "now it works"})
    res = granola_sync.main(root=env["root"])
    assert res["new"] == 1
    assert "flaky-2" in _ledger(env)
    assert _status(env)["pending"] == {}


def test_batch_exception_does_not_abort_run(env, monkeypatch):
    env["use"]([_m("a", 3), _m("b", 2), _m("c", 1)], {"a": "A", "b": "B", "c": "C"})
    real = granola_sync._fetch_transcripts

    def boom_on_first(batch, root=None):
        if any(m["id"] == "a" for m in batch):
            raise TimeoutError("claude -p timed out")
        return real(batch, root)
    monkeypatch.setattr(granola_sync, "_fetch_transcripts", boom_on_first)
    res = granola_sync.main(root=env["root"])
    assert res["failed"] >= 1 and res["new"] >= 1
    assert "a" in _status(env)["pending"]


def test_gives_up_after_max_attempts(env):
    env["st"].mkdir(parents=True)
    pend = {"never": {"id": "never", "title": "X", "created_at": _iso(NOW),
                      "attempts": granola_sync.MAX_ATTEMPTS - 1}}
    (env["st"] / "granola_status.json").write_text(json.dumps(
        {"last_success": _iso(NOW - timedelta(hours=1)), "pending": pend}))
    env["use"]([], {})
    granola_sync.main(root=env["root"])
    assert "never" not in _status(env)["pending"]
    assert _ledger(env)["never"]["skipped"]               # recorded, never retried again


def test_progress_saved_per_meeting(env, monkeypatch):
    """A crash mid-run must not lose meetings already written."""
    env["use"]([_m("first", 2), _m("second", 1)], {"first": "1", "second": "2"})
    n = {"i": 0}

    def _downstream(txt, mid, state, log):
        n["i"] += 1
        if n["i"] == 2:
            raise KeyboardInterrupt
        return str(txt)
    monkeypatch.setattr(granola_sync.transcript_post, "run_downstream", _downstream)
    with pytest.raises(KeyboardInterrupt):
        granola_sync.main(root=env["root"])
    assert "first" in _ledger(env)


def test_overlapping_run_is_skipped(env):
    env["use"]([], {})
    env["st"].mkdir(parents=True)
    with open(env["st"] / "granola_sync.lock", "a+") as fh:
        assert granola_sync.platform_lib.lock(fh, blocking=False)
        try:
            res = granola_sync.main(root=env["root"])
        finally:
            granola_sync.platform_lib.unlock(fh)
    assert res["status"] == "skipped" and "running" in res["reason"]
    assert env["calls"]["list"] == []


# ── parsing ─────────────────────────────────────────────────────────────────

def test_parse_fetch_output_handles_wrapped_and_malformed():
    assert granola_sync._parse_fetch_output('{"result": "[{\\"id\\": \\"a\\"}]"}') == [{"id": "a"}]
    assert granola_sync._parse_fetch_output('[{"id": "b"}]') == [{"id": "b"}]
    assert granola_sync._parse_fetch_output('here you go: [{"id": "c"}] done') == [{"id": "c"}]
    assert granola_sync._parse_fetch_output("not json at all") is None
    assert granola_sync._parse_fetch_output("") is None
    assert granola_sync._parse_fetch_output('{"result": null}') is None
    assert granola_sync._parse_fetch_output('{"result": 42}') is None
    assert granola_sync._parse_fetch_output('{"result": {"x": 1}}') is None
    assert granola_sync._parse_fetch_output('{"result": [{"id": "z"}]}') == [{"id": "z"}]


def test_parse_list_output_error_object_is_needs_reauth():
    with pytest.raises(granola_sync.GranolaError) as ei:
        granola_sync._parse_list_output(
            json.dumps({"result": '{"error": "granola_unavailable", "detail": "not connected"}'}))
    assert ei.value.kind == "needs_reauth"
    assert granola_sync._parse_list_output(json.dumps({"result": "[]"})) == []


def _stream(*events):
    return "\n".join(json.dumps(e) for e in events)


def test_parse_stream_transcripts_reads_raw_tool_results():
    tool = "mcp__claude_ai_Granola__get_meeting_transcript"
    out = _stream(
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu1", "name": tool, "input": {"meeting_id": "m1"}},
            {"type": "tool_use", "id": "tu2", "name": tool, "input": {"meeting_id": "m2"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu1",
             "content": [{"type": "text", "text": "Ann: “quoted” words"}]},
            {"type": "tool_result", "tool_use_id": "tu2", "is_error": True,
             "content": "transcript unavailable"}]}},
        {"type": "result", "subtype": "success", "result": "DONE"},
    )
    got = granola_sync._parse_stream_transcripts(out)
    assert got == {"m1": "Ann: “quoted” words"}


def test_parse_stream_transcripts_unwraps_json_payload():
    tool = "mcp__claude_ai_Granola__get_meeting_transcript"
    payload = json.dumps({"id": "m1", "transcript": [
        {"speaker": "Ann", "text": "hi"}, {"speaker": "Bob", "text": "yo"}]})
    out = _stream(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t", "name": tool, "input": {"meeting_id": "m1"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "content": payload}]}})
    assert granola_sync._parse_stream_transcripts(out) == {"m1": "Ann: hi\nBob: yo"}


def test_basename_unique_per_meeting_id():
    b1, _ = granola_sync._basename("2026-06-08T10:00:00Z", "Sync", "aaaaaaaa-1111")
    b2, _ = granola_sync._basename("2026-06-08T10:00:00Z", "Sync", "bbbbbbbb-2222")
    assert b1 != b2
    assert b1.endswith("_aaaaaaaa") and b2.endswith("_bbbbbbbb")
    b3, _ = granola_sync._basename("2026-06-08T10:00:00Z", "Sync", None)
    assert b3.endswith("_Sync")


# ── the real subprocess seams: utf-8, per-call timeout, stdin-safe ─────────

def test_fetch_transcripts_subprocess_kwargs(env, monkeypatch):
    import subprocess
    seen = []
    tool = "mcp__claude_ai_Granola__get_meeting_transcript"

    def fake_run(cmd, **kw):
        seen.append((cmd, kw))
        out = _stream(
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t", "name": tool, "input": {"meeting_id": "m1"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t", "content": "Ann: hello"}]}})
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    monkeypatch.setattr(granola_sync.subprocess, "run", fake_run)
    got = granola_sync._fetch_transcripts([{"id": "m1"}], root=env["root"])
    assert got == {"m1": "Ann: hello"}
    cmd, kw = seen[0]
    assert kw["encoding"] == "utf-8" and kw["timeout"] == granola_sync.FETCH_TIMEOUT
    assert "stream-json" in cmd and "--verbose" in cmd
    assert int(kw["env"]["MAX_MCP_OUTPUT_TOKENS"]) >= 50000
