import sys

import magnolia


def _patch(monkeypatch, *, running, explicit=8742, available=True):
    """Stub the launcher seams.

    explicit: value configured_server_port() returns (None = unconfigured).
    available: whether port 8742 (and any probed port) reports free.
    """
    calls = {"start": 0, "install": 0, "opened": None, "set_port": None}
    monkeypatch.setattr(magnolia.server_lib, "is_running", lambda *a, **k: running)
    monkeypatch.setattr(magnolia.server_lib, "start", lambda *a, **k: calls.__setitem__("start", calls["start"] + 1))
    monkeypatch.setattr(magnolia.server_lib, "default_cmd", lambda: ["python", "task_server.py"])
    monkeypatch.setattr(magnolia.server_lib, "port_available", lambda p: available)
    monkeypatch.setattr(magnolia.server_lib, "free_port", lambda: 50000)
    monkeypatch.setattr(magnolia.profile_lib, "configured_server_port", lambda *a, **k: explicit)
    monkeypatch.setattr(magnolia.profile_lib, "set_server_port", lambda p, *a, **k: calls.__setitem__("set_port", p))
    monkeypatch.setattr(magnolia.persist_lib, "is_installed", lambda: True)
    monkeypatch.setattr(magnolia.persist_lib, "install", lambda **k: calls.__setitem__("install", calls["install"] + 1))
    monkeypatch.setattr(magnolia.platform_lib, "open_url", lambda u: calls.__setitem__("opened", u))
    return calls


def test_launch_starts_server_when_not_running(monkeypatch):
    # explicit port configured -> respect it, do not re-pick.
    calls = _patch(monkeypatch, running=False, explicit=8742)
    res = magnolia.launch()
    assert calls["start"] == 1
    assert calls["opened"] == "http://localhost:8742"
    assert res["url"] == "http://localhost:8742"
    assert res["port"] == 8742
    assert res["started"] is True
    assert calls["set_port"] is None     # explicit choice never re-persisted


def test_launch_skips_start_when_already_running(monkeypatch):
    calls = _patch(monkeypatch, running=True, explicit=8742)
    res = magnolia.launch()
    assert calls["start"] == 0           # did not double-start
    assert calls["opened"] == "http://localhost:8742"   # still opens browser
    assert res["started"] is False


def test_launch_installs_persistence_when_absent(monkeypatch):
    calls = _patch(monkeypatch, running=True, explicit=8742)
    monkeypatch.setattr(magnolia.persist_lib, "is_installed", lambda: False)
    magnolia.launch()
    assert calls["install"] == 1


def test_launch_can_skip_browser(monkeypatch):
    calls = _patch(monkeypatch, running=True, explicit=8742)
    magnolia.launch(open_browser=False)
    assert calls["opened"] is None


def test_launch_targets_explicit_port_without_persisting(monkeypatch):
    calls = _patch(monkeypatch, running=False, explicit=8801)
    res = magnolia.launch()
    assert res["port"] == 8801
    assert res["url"] == "http://localhost:8801"
    assert calls["set_port"] is None     # deliberate choice, not re-persisted


def test_launch_claims_8742_when_free_and_unconfigured(monkeypatch):
    # unconfigured + 8742 free -> take the canonical default and persist it.
    calls = _patch(monkeypatch, running=False, explicit=None, available=True)
    res = magnolia.launch()
    assert res["port"] == 8742
    assert calls["set_port"] == 8742


def test_launch_picks_fallback_when_8742_taken(monkeypatch):
    # unconfigured + 8742 (and probed ports) busy -> OS-assigned free port,
    # persisted, and reflected in url/port.
    calls = _patch(monkeypatch, running=False, explicit=None, available=False)
    monkeypatch.setattr(magnolia.server_lib, "free_port", lambda: 50123)
    res = magnolia.launch()
    assert res["port"] == 50123
    assert res["url"] == "http://localhost:50123"
    assert calls["set_port"] == 50123


def test_launch_picks_first_free_in_range(monkeypatch):
    # 8742 busy, but a port in the 8744-8779 range is free -> take it (skips 8743).
    calls = _patch(monkeypatch, running=False, explicit=None)
    monkeypatch.setattr(magnolia.server_lib, "port_available",
                        lambda p: p == 8745)   # 8742 busy, 8744 busy, 8745 free
    res = magnolia.launch()
    assert res["port"] == 8745
    assert calls["set_port"] == 8745


def test_launch_does_not_reuse_foreign_board(monkeypatch):
    # is_running(target) False means OUR board on the chosen port isn't up yet,
    # so we start it (never piggyback on whatever is on 8742).
    calls = _patch(monkeypatch, running=False, explicit=None, available=False)
    monkeypatch.setattr(magnolia.server_lib, "free_port", lambda: 50500)
    magnolia.launch()
    assert calls["start"] == 1


def test_update_runs_ff_only_pull_in_repo(monkeypatch):
    seen = {}
    monkeypatch.setattr(magnolia, "_run",
                        lambda cmd: seen.update(cmd=cmd) or (0, "Already up to date.\n"))
    res = magnolia.update()
    assert seen["cmd"][:3] == ["git", "-C", magnolia.PM_OS_DIR]
    assert "pull" in seen["cmd"] and "--ff-only" in seen["cmd"]
    assert res["status"] == "ok"
    assert "up to date" in res["output"].lower()


def test_update_reports_failure(monkeypatch):
    monkeypatch.setattr(magnolia, "_run", lambda cmd: (1, "fatal: not possible to fast-forward\n"))
    res = magnolia.update()
    assert res["status"] == "failed"
    assert "fast-forward" in res["output"]


def test_doctor_runs_detection(monkeypatch):
    seen = {}
    monkeypatch.setattr(magnolia, "_run",
                        lambda cmd: seen.update(cmd=cmd) or (0, '{"capabilities": {}}'))
    res = magnolia.doctor()
    assert seen["cmd"][0] == sys.executable
    assert seen["cmd"][1].endswith("doctor.py")
    assert seen["cmd"][2] == "detect"
    assert res["status"] == "ok"


def test_main_no_subcommand_launches(monkeypatch):
    hit = {}
    monkeypatch.setattr(magnolia, "launch", lambda **k: (hit.__setitem__("launch", True), {"url": "x", "started": True})[1])
    assert magnolia._main([]) == 0
    assert hit.get("launch") is True


def test_main_update_routes_to_update(monkeypatch):
    hit = {}
    monkeypatch.setattr(magnolia, "update", lambda: (hit.__setitem__("update", True), {"status": "ok", "output": ""})[1])
    assert magnolia._main(["update"]) == 0
    assert hit.get("update") is True


def test_main_doctor_routes_to_doctor(monkeypatch):
    hit = {}
    monkeypatch.setattr(magnolia, "doctor", lambda: (hit.__setitem__("doctor", True), {"status": "ok", "output": ""})[1])
    assert magnolia._main(["doctor"]) == 0
    assert hit.get("doctor") is True


def test_main_update_failure_returns_nonzero(monkeypatch):
    monkeypatch.setattr(magnolia, "update", lambda: {"status": "failed", "output": "boom"})
    assert magnolia._main(["update"]) == 1


def test_main_launch_failure_is_clean(monkeypatch, capsys):
    def boom(**k):
        raise TimeoutError("server did not come up")
    monkeypatch.setattr(magnolia, "launch", boom)
    rc = magnolia._main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "doctor" in out.lower()   # points the user at the remedy
