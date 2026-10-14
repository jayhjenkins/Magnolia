import re
import task_dispatch as td


class _FakeProc:
    """Minimal subprocess.Popen stand-in: exits cleanly on the first poll()."""
    def __init__(self, *args, **kwargs):
        self.pid = 4242

    def poll(self):
        return 0  # terminal code -> dispatch poll loop breaks immediately

    def wait(self, timeout=None):
        return 0


def test_persist_failure_does_not_destabilize_dispatch(monkeypatch):
    """A session-id persistence failure must NOT affect a launched dispatch.

    Real-path test: drive dispatch_task with a fake Popen that exits cleanly,
    then make task_lib.update_task raise FileNotFoundError. The launch already
    succeeded, so dispatch must still report success — never the misleading
    "claude not found" error, and never propagate the persistence exception.
    """
    monkeypatch.setattr("profile_lib.cost_posture", lambda root=None: "balanced")
    monkeypatch.setattr(td.subprocess, "Popen", _FakeProc)
    # read_task is consulted in the poll/finalize path; keep it benign.
    monkeypatch.setattr(td.task_lib, "read_task",
                        lambda task_id: {"frontmatter": {}})
    # The persistence write blows up — exactly the failure we must isolate.
    def _boom(*a, **k):
        raise FileNotFoundError("boom")
    monkeypatch.setattr(td.task_lib, "update_task", _boom)

    result = td.dispatch_task({"id": "TASK-1", "title": "t", "priority": "medium"})

    assert result["success"] is True
    assert result["error"] is None
    assert result["task_id"] == "TASK-1"


def test_persist_session_id_swallows_update_failure(monkeypatch):
    """The best-effort helper itself must never raise when update_task fails."""
    def _boom(*a, **k):
        raise FileNotFoundError("boom")
    monkeypatch.setattr(td.task_lib, "update_task", _boom)
    # Should return cleanly (no exception escapes).
    assert td._persist_session_id("TASK-1", "sid-123") is None


def test_build_claude_cmd_includes_session_id():
    cmd, sid = td.build_claude_cmd(prompt="x", model="m", tools_str="Read(*)", max_turns="30")
    assert "--session-id" in cmd
    assert cmd[cmd.index("--session-id") + 1] == sid
    assert re.match(r"[0-9a-f-]{36}", sid)


def test_build_claude_cmd_prompt_is_first_positional():
    # GOTCHA: --allowedTools is variadic and will swallow a trailing prompt.
    # The prompt MUST be the first positional arg (right after "claude").
    cmd, _ = td.build_claude_cmd(prompt="my prompt", model="m", tools_str="Read(*)", max_turns="30")
    assert cmd[0] == "claude"
    assert cmd[1] == "my prompt"


def test_build_claude_cmd_accepts_explicit_session_id():
    cmd, sid = td.build_claude_cmd(prompt="x", model="m", tools_str="Read(*)", max_turns="30", session_id="FIXED-SID")
    assert sid == "FIXED-SID"
    assert cmd[cmd.index("--session-id") + 1] == "FIXED-SID"


def test_dispatch_marks_running_after_launch(monkeypatch):
    """Regression: a worker that never ran agent:start left agent_status empty,
    so the dispatch-sweep re-dispatched it every 60s (four duplicate workers).
    dispatch_task must stamp agent_status=running once the launch succeeds."""
    monkeypatch.setattr("profile_lib.cost_posture", lambda root=None: "balanced")
    monkeypatch.setattr(td.subprocess, "Popen", _FakeProc)
    monkeypatch.setattr(td.task_lib, "read_task",
                        lambda task_id: {"frontmatter": {"agent_status": None}})
    updates = []
    monkeypatch.setattr(td.task_lib, "update_task",
                        lambda task_id, changes, **k: updates.append(changes))

    td.dispatch_task({"id": "TASK-1", "title": "t", "priority": "medium"})

    assert any(u.get("agent_status") == "running" for u in updates)


def test_mark_running_leaves_existing_status(monkeypatch):
    monkeypatch.setattr(td.task_lib, "read_task",
                        lambda task_id: {"frontmatter": {"agent_status": "queued"}})
    updates = []
    monkeypatch.setattr(td.task_lib, "update_task",
                        lambda task_id, changes, **k: updates.append(changes))
    td._mark_running("TASK-1")
    assert updates == []


def test_mark_running_swallows_failure(monkeypatch):
    def _boom(*a, **k):
        raise FileNotFoundError("boom")
    monkeypatch.setattr(td.task_lib, "read_task", _boom)
    assert td._mark_running("TASK-1") is None
