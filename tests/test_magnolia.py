import sys

import magnolia


def _patch(monkeypatch, *, running):
    calls = {"start": 0, "install": 0, "opened": None}
    monkeypatch.setattr(magnolia.server_lib, "is_running", lambda *a, **k: running)
    monkeypatch.setattr(magnolia.server_lib, "start", lambda *a, **k: calls.__setitem__("start", calls["start"] + 1))
    monkeypatch.setattr(magnolia.server_lib, "url", lambda *a, **k: "http://localhost:8742")
    monkeypatch.setattr(magnolia.server_lib, "default_cmd", lambda: ["python", "task_server.py"])
    monkeypatch.setattr(magnolia.persist_lib, "is_installed", lambda: True)
    monkeypatch.setattr(magnolia.persist_lib, "install", lambda **k: calls.__setitem__("install", calls["install"] + 1))
    monkeypatch.setattr(magnolia.platform_lib, "open_url", lambda u: calls.__setitem__("opened", u))
    return calls


def test_launch_starts_server_when_not_running(monkeypatch):
    calls = _patch(monkeypatch, running=False)
    res = magnolia.launch()
    assert calls["start"] == 1
    assert calls["opened"] == "http://localhost:8742"
    assert res["url"] == "http://localhost:8742"
    assert res["started"] is True


def test_launch_skips_start_when_already_running(monkeypatch):
    calls = _patch(monkeypatch, running=True)
    res = magnolia.launch()
    assert calls["start"] == 0           # did not double-start
    assert calls["opened"] == "http://localhost:8742"   # still opens browser
    assert res["started"] is False


def test_launch_installs_persistence_when_absent(monkeypatch):
    calls = _patch(monkeypatch, running=True)
    monkeypatch.setattr(magnolia.persist_lib, "is_installed", lambda: False)
    magnolia.launch()
    assert calls["install"] == 1


def test_launch_can_skip_browser(monkeypatch):
    calls = _patch(monkeypatch, running=True)
    magnolia.launch(open_browser=False)
    assert calls["opened"] is None


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
