import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cron_lib  # noqa: E402


def test_auto_dispatch_uses_headless_env(monkeypatch):
    # Cron dispatch goes through task_server._spawn_task_dispatch (the
    # scheduler's dispatch_fn); execute_job no longer spawns on its own.
    import task_server
    seen = {}
    monkeypatch.setattr(task_server.platform_lib, "headless_claude_env",
                        lambda: {"PATH": "SENTINEL"})

    def fake_popen(cmd, **k):
        seen["env"] = k.get("env")

        class P:
            pass

        return P()

    monkeypatch.setattr(task_server.subprocess, "Popen", fake_popen)
    task_server._spawn_task_dispatch("TASK-9999")
    assert seen["env"]["PATH"] == "SENTINEL"  # no hand-rolled colon PATH
