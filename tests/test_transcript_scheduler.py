"""TranscriptScheduler: the board server runs the configured transcript sync
hourly on every OS - unless an external launchd agent already does (macOS).

Never starts a thread or a real subprocess: tick() is called directly with an
injected popen."""
import os
import sys

import transcript_sync


class FakeProc:
    def __init__(self, cmd, **kw):
        self.cmd, self.kw, self.rc, self.pid = cmd, kw, None, 4242

    def poll(self):
        return self.rc


def _profile(tmp_path, provider="granola", external=False):
    (tmp_path / "profile").mkdir(exist_ok=True)
    body = f"transcript:\n  provider: {provider}\n"
    if external:
        body += "  external_feed: true\n"
    (tmp_path / "profile" / "integrations.yaml").write_text(body)


def _sched(tmp_path, monkeypatch, la_dir=None, **kw):
    spawned = []

    def popen(cmd, **pkw):
        p = FakeProc(cmd, **pkw)
        spawned.append(p)
        return p
    monkeypatch.setattr(transcript_sync.profile_lib, "PM_OS_DIR", str(tmp_path))
    monkeypatch.setattr(transcript_sync.platform_lib, "launch_agents_dir",
                        lambda: str(la_dir) if la_dir else None)
    s = transcript_sync.TranscriptScheduler(root=str(tmp_path), popen=popen, **kw)
    return s, spawned


def _plist(d, label, body="granola_sync.py"):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{label}.plist").write_text(
        f"<plist><dict><key>Label</key><string>{label}</string>"
        f"<key>ProgramArguments</key><array><string>{body}</string></array></dict></plist>")


def test_tick_runs_sync_in_subprocess_with_running_interpreter(tmp_path, monkeypatch):
    _profile(tmp_path)
    s, spawned = _sched(tmp_path, monkeypatch)
    assert s.tick() == "started"
    cmd = spawned[0].cmd
    assert cmd[0] == sys.executable
    assert os.path.basename(cmd[1]) == "transcript_sync.py"
    assert spawned[0].kw["env"]["PYTHONIOENCODING"] == "utf-8"
    # stdout goes to the provider's log
    assert (tmp_path / "logs" / "granola_sync.log").exists()


def test_tick_skips_when_no_provider(tmp_path, monkeypatch):
    _profile(tmp_path, provider="none")
    s, spawned = _sched(tmp_path, monkeypatch)
    assert s.tick() == "skipped:no-provider"
    assert spawned == []


def test_tick_never_overlaps_a_running_sync(tmp_path, monkeypatch):
    _profile(tmp_path)
    s, spawned = _sched(tmp_path, monkeypatch)
    assert s.tick() == "started"
    assert s.tick() == "skipped:running"
    spawned[0].rc = 0                       # finished
    assert s.tick() == "started"
    assert len(spawned) == 2


def test_hung_sync_is_killed_after_max_runtime(tmp_path, monkeypatch):
    _profile(tmp_path)
    killed = []
    monkeypatch.setattr(transcript_sync.platform_lib, "kill_process_group",
                        lambda p: killed.append(p))
    s, spawned = _sched(tmp_path, monkeypatch, max_runtime=10)
    clock = {"t": 1000.0}
    monkeypatch.setattr(transcript_sync.time, "monotonic", lambda: clock["t"])
    assert s.tick() == "started"
    clock["t"] += 5
    assert s.tick() == "skipped:running" and killed == []
    clock["t"] += 10
    assert s.tick() == "started"
    assert killed == [spawned[0]]


def test_tick_skips_when_launchd_agent_already_runs_the_feed(tmp_path, monkeypatch):
    _profile(tmp_path)
    la = tmp_path / "LaunchAgents"
    _plist(la, "com.magnolia.granolasync")
    s, spawned = _sched(tmp_path, monkeypatch, la_dir=la)
    assert s.tick() == "skipped:external"
    assert spawned == []


def test_tick_skips_for_a_legacy_otter_launchd_agent(tmp_path, monkeypatch):
    _profile(tmp_path, provider="otter")
    la = tmp_path / "LaunchAgents"
    _plist(la, "com.someone.ottersync", body="/Users/x/pm-os/scripts/otter_sync.py")
    s, spawned = _sched(tmp_path, monkeypatch, la_dir=la)
    assert s.tick() == "skipped:external"


def test_unrelated_launch_agents_do_not_block(tmp_path, monkeypatch):
    _profile(tmp_path)
    la = tmp_path / "LaunchAgents"
    _plist(la, "com.example.backup", body="/usr/local/bin/backup")
    s, spawned = _sched(tmp_path, monkeypatch, la_dir=la)
    assert s.tick() == "started"


def test_disabled_agent_backup_does_not_block(tmp_path, monkeypatch):
    # feed_guard.disable() renames a plist aside; launchd no longer runs it.
    _profile(tmp_path)
    la = tmp_path / "LaunchAgents"
    _plist(la, "com.magnolia.granolasync")
    os.rename(la / "com.magnolia.granolasync.plist",
              la / "com.magnolia.granolasync.plist.disabled-by-magnolia")
    s, spawned = _sched(tmp_path, monkeypatch, la_dir=la)
    assert s.tick() == "started"


def test_tick_skips_when_profile_marks_external_feed(tmp_path, monkeypatch):
    _profile(tmp_path, external=True)
    s, spawned = _sched(tmp_path, monkeypatch)
    assert s.tick() == "skipped:external"


def test_windows_has_no_launch_agents_so_server_schedules(tmp_path, monkeypatch):
    _profile(tmp_path)
    s, spawned = _sched(tmp_path, monkeypatch, la_dir=None)   # launch_agents_dir() -> None
    assert s.tick() == "started"


def test_spawn_failure_is_contained(tmp_path, monkeypatch):
    _profile(tmp_path)

    def boom(cmd, **kw):
        raise OSError("no python")
    monkeypatch.setattr(transcript_sync.profile_lib, "PM_OS_DIR", str(tmp_path))
    monkeypatch.setattr(transcript_sync.platform_lib, "launch_agents_dir", lambda: None)
    s = transcript_sync.TranscriptScheduler(root=str(tmp_path), popen=boom)
    assert s.tick() == "error"


def test_task_server_starts_the_transcript_scheduler():
    src = open(os.path.join(os.path.dirname(__file__), "..", "scripts", "task_server.py"),
               encoding="utf-8").read()
    assert "TranscriptScheduler(" in src and "transcript_scheduler.start()" in src
    assert "transcript_scheduler.stop()" in src
