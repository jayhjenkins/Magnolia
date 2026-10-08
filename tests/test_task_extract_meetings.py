import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import task_extract_meetings as tem  # noqa: E402


def test_resolve_relative_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tem, "PM_OS_DIR", tmp_path)
    f = tmp_path / "datasets" / "meetings" / "x.md"
    f.parent.mkdir(parents=True)
    f.write_text("hi")
    assert tem.resolve_path("datasets/meetings/x.md") == f.resolve()

def test_resolve_absolute_posix_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tem, "PM_OS_DIR", tmp_path)
    f = tmp_path / "a.md"; f.write_text("hi")
    assert tem.resolve_path(str(f)) == f.resolve()

def test_resolve_windows_drive_path_not_doubled(tmp_path, monkeypatch):
    # The bug: bash treated C:\... as relative and prepended PM_OS_DIR.
    # pathlib must treat a drive-absolute path as absolute (no doubling).
    monkeypatch.setattr(tem, "PM_OS_DIR", tmp_path)
    p = tem.resolve_path("C:/Users/Josh/x.md")
    assert str(p).replace("\\", "/").endswith("Users/Josh/x.md")
    assert "datasets" not in str(p)  # PM_OS_DIR was NOT prepended

def test_processed_idempotency(tmp_path, monkeypatch):
    monkeypatch.setattr(tem, "PM_OS_DIR", tmp_path)
    pf = tmp_path / "datasets" / "tasks" / "_processed-meetings.txt"
    pf.parent.mkdir(parents=True)
    monkeypatch.setattr(tem, "PROCESSED_FILE", pf)
    assert tem.is_processed("datasets/meetings/x.md") is False
    tem.mark_processed("datasets/meetings/x.md")
    assert tem.is_processed("datasets/meetings/x.md") is True
    tem.mark_processed("datasets/meetings/x.md")  # second call no-op
    assert pf.read_text().count("x.md") == 1


# ── prompt points at the Python CLI, never the bash wrapper ────────────────

def test_prompt_uses_running_interpreter_and_task_cli(tmp_path):
    from pathlib import Path
    p = tem._prompt(tmp_path / "m.md")
    py = Path(sys.executable).as_posix()
    assert f'"{py}"' in p                            # quoted: survives spaces
    assert "task_cli.py" in p
    assert "./scripts/task" + ".sh list" not in p    # no bash wrapper invocation


# ── never mark a meeting processed when extraction failed ──────────────────

def _setup(tmp_path, monkeypatch, stdout, rc=0):
    import subprocess
    monkeypatch.setattr(tem, "PM_OS_DIR", tmp_path)
    pf = tmp_path / "datasets" / "tasks" / "_processed-meetings.txt"
    monkeypatch.setattr(tem, "PROCESSED_FILE", pf)
    f = tmp_path / "datasets" / "meetings" / "m.md"
    f.parent.mkdir(parents=True)
    f.write_text("hi", encoding="utf-8")
    monkeypatch.setattr(tem, "_snapshot_task_ids", lambda: set())
    monkeypatch.setattr(tem, "_dispatch_new_tasks", lambda before: 0)
    monkeypatch.setattr(tem.profile_lib, "resolve_model", lambda *a, **k: "sonnet")
    monkeypatch.setattr(tem.profile_lib, "harness", lambda root=None: "claude")
    seen = {}

    def fake_run(cmd, **kw):
        seen["kw"] = kw
        return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")
    monkeypatch.setattr(tem.subprocess, "run", fake_run)
    return f, seen


def test_success_marks_processed(tmp_path, monkeypatch):
    import json
    out = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": "Created 2 tasks.\nEXTRACT_STATUS: OK"})
    f, seen = _setup(tmp_path, monkeypatch, out)
    assert tem.process_transcript(str(f)) == 0
    assert tem.is_processed("datasets/meetings/m.md")
    assert seen["kw"]["encoding"] == "utf-8"


def test_claude_error_result_not_marked(tmp_path, monkeypatch):
    import json
    out = json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True})
    f, _ = _setup(tmp_path, monkeypatch, out)
    assert tem.process_transcript(str(f)) == 1
    assert not tem.is_processed("datasets/meetings/m.md")


def test_reported_task_cli_failure_not_marked(tmp_path, monkeypatch):
    import json
    out = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "result": "EXTRACT_STATUS: FAILED task_cli add exited 1"})
    f, _ = _setup(tmp_path, monkeypatch, out)
    assert tem.process_transcript(str(f)) == 1
    assert not tem.is_processed("datasets/meetings/m.md")


def test_nonzero_exit_not_marked(tmp_path, monkeypatch):
    f, _ = _setup(tmp_path, monkeypatch, "", rc=1)
    assert tem.process_transcript(str(f)) == 1
    assert not tem.is_processed("datasets/meetings/m.md")
