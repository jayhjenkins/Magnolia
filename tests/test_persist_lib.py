import persist_lib


def test_render_plist_has_no_hardcoded_user_and_uses_repo_path(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/testuser")
    plist = persist_lib.render_launchagent(
        label="com.pm-os.task-server",
        program=["/usr/bin/python3", "/repo/scripts/task_server.py"],
        working_dir="/repo",
        log_path="/repo/logs/task-server.log",
    )
    assert "/Users/jayjenkins" not in plist
    assert "<key>RunAtLoad</key>" in plist and "<true/>" in plist
    assert "<key>KeepAlive</key>" in plist
    assert "/repo/scripts/task_server.py" in plist
    assert "com.pm-os.task-server" in plist


def test_render_plist_path_includes_current_users_local_bin(monkeypatch):
    # launchd's own PATH omits ~/.local/bin (where mgc, claude, etc. live);
    # this must resolve against whoever is installing, not a baked-in developer path.
    monkeypatch.setenv("HOME", "/Users/testuser")
    plist = persist_lib.render_launchagent(
        label="com.pm-os.task-server",
        program=["/usr/bin/python3", "/repo/scripts/task_server.py"],
        working_dir="/repo",
        log_path="/repo/logs/task-server.log",
    )
    assert "/Users/testuser/.local/bin" in plist


def test_render_scheduled_task_at_logon():
    ps = persist_lib.render_scheduled_task(
        name="MagnoliaTaskServer",
        program="C:\\Python\\python.exe",
        args="C:\\repo\\scripts\\task_server.py",
        working_dir="C:\\repo",
    )
    assert "Register-ScheduledTask" in ps
    assert "-AtLogOn" in ps
    assert "MagnoliaTaskServer" in ps
    # per-user, no admin: interactive token, not SYSTEM
    assert "-RunLevel Highest" not in ps


def test_install_macos_writes_plist(tmp_path, monkeypatch):
    monkeypatch.setattr(persist_lib.platform_lib, "os_kind", lambda: "darwin")
    monkeypatch.setattr(persist_lib.platform_lib, "launch_agents_dir", lambda: str(tmp_path))
    result = persist_lib.install(program=["/usr/bin/python3", "/repo/scripts/task_server.py"],
                                 working_dir="/repo", log_path="/repo/logs/s.log",
                                 activate=False)
    plist_path = tmp_path / f"{persist_lib.LABEL}.plist"
    assert plist_path.is_file()
    assert persist_lib.is_installed() is True
    assert result["mechanism"] == "launchagent"


def test_is_installed_false_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(persist_lib.platform_lib, "os_kind", lambda: "darwin")
    monkeypatch.setattr(persist_lib.platform_lib, "launch_agents_dir", lambda: str(tmp_path))
    assert persist_lib.is_installed() is False


def test_install_windows_returns_command(monkeypatch):
    monkeypatch.setattr(persist_lib.platform_lib, "os_kind", lambda: "windows")
    result = persist_lib.install(program=["python.exe", "task_server.py"],
                                 working_dir="C:\\repo", log_path="x", activate=False)
    assert result["mechanism"] == "scheduled_task"
    assert "Register-ScheduledTask" in result["command"]


def test_install_rejects_empty_program(monkeypatch):
    monkeypatch.setattr(persist_lib.platform_lib, "os_kind", lambda: "darwin")
    import pytest
    with pytest.raises(ValueError):
        persist_lib.install(program=[], working_dir="/r", log_path="/r/l.log", activate=False)


def test_install_macos_reports_activation_success(tmp_path, monkeypatch):
    monkeypatch.setattr(persist_lib.platform_lib, "os_kind", lambda: "darwin")
    monkeypatch.setattr(persist_lib.platform_lib, "launch_agents_dir", lambda: str(tmp_path))
    import subprocess as _sp
    monkeypatch.setattr(persist_lib.subprocess, "run",
                        lambda *a, **k: _sp.CompletedProcess(a[0], 0, stdout=b"", stderr=b""))
    result = persist_lib.install(program=["/usr/bin/python3", "/repo/scripts/task_server.py"],
                                 working_dir="/repo", log_path="/repo/logs/s.log", activate=True)
    assert result["mechanism"] == "launchagent"
    assert result["activated"] is True


def test_install_macos_reports_activation_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(persist_lib.platform_lib, "os_kind", lambda: "darwin")
    monkeypatch.setattr(persist_lib.platform_lib, "launch_agents_dir", lambda: str(tmp_path))
    import subprocess as _sp
    monkeypatch.setattr(persist_lib.subprocess, "run",
                        lambda *a, **k: _sp.CompletedProcess(a[0], 1, stdout=b"", stderr=b"Load failed: bad plist"))
    result = persist_lib.install(program=["/usr/bin/python3", "/repo/scripts/task_server.py"],
                                 working_dir="/repo", log_path="/repo/logs/s.log", activate=True)
    assert result["mechanism"] == "launchagent"
    assert result["activated"] is False
    assert "activation_error" in result
