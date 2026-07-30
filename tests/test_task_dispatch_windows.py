import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import task_dispatch  # noqa: E402


def test_actionable_query_uses_python_not_bash(monkeypatch):
    captured = {}

    class R:
        returncode = 0
        stdout = "[]"

    def fake_run(cmd, **k):
        captured["cmd"] = cmd
        return R()

    monkeypatch.setattr(task_dispatch.subprocess, "run", fake_run)
    task_dispatch.get_actionable_tasks()
    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][1].endswith("task_cli.py")
    assert not any(str(c).endswith("task.sh") for c in captured["cmd"])


def test_human_decision_card_types_never_dispatched(monkeypatch):
    """Regression for the 2026-07-30 VNT-100 incident: a stalled Jira receipt
    (card_type: receipt, status stuck at "open") got swept by the background
    dispatch scheduler and redispatched to a worker with nothing to do,
    repeatedly. Card types that represent a human decision -- accept/reject,
    keep/undo, confirm/reject, graduate -- must never be treated as
    actionable agent work, regardless of status."""
    import json

    def _task(id_, card_type=None, status="open"):
        t = {"id": id_, "title": "t", "status": status, "priority": "medium",
             "created": "2026-01-01T00:00:00Z", "agent_status": None}
        if card_type:
            t["card_type"] = card_type
        return t

    tasks = [
        _task("TASK-1", card_type=None),          # plain task -> actionable
        _task("TASK-2", card_type="receipt"),
        _task("TASK-3", card_type="confirm"),
        _task("TASK-4", card_type="graduation"),
        _task("TASK-5", card_type="recommendation"),
        _task("TASK-6", card_type="program-setup"),
    ]

    class R:
        returncode = 0
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run(cmd, **k):
        # First call is the "agent" queue, second is "collab" -- return the
        # full set once, empty the second time, so no task is double-counted.
        if not fake_run.called:
            fake_run.called = True
            return R(json.dumps(tasks))
        return R("[]")
    fake_run.called = False

    monkeypatch.setattr(task_dispatch.subprocess, "run", fake_run)
    actionable = task_dispatch.get_actionable_tasks()
    ids = {t["id"] for t in actionable}
    assert ids == {"TASK-1"}
