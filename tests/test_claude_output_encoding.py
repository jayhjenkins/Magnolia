"""Regression: every subprocess that captures `claude` output decodes UTF-8.

`text=True` with no encoding decodes with the locale codec — cp1252 on Windows —
and crashes with UnicodeDecodeError on the curly quotes / em dashes claude emits.
Call sites must use **platform_lib.text_kwargs() (text + encoding=utf-8 +
errors=replace) instead of a bare text=True."""
import os
import re

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")

# Files that capture headless claude output.
CLAUDE_CALLERS = [
    "granola_sync.py",
    "otter_classify.py",
    "sentinel_runner.py",
    "judge.py",
    "chat_runner.py",
    "cadence/reconcile.py",
    "jira_publish.py",
    "parse_task_input.py",
    "task_extract_meetings.py",
]


@pytest.mark.parametrize("rel", CLAUDE_CALLERS)
def test_no_locale_decoded_subprocess_text(rel):
    with open(os.path.join(SCRIPTS, rel), encoding="utf-8") as f:
        src = f.read()
    bare = [m.start() for m in re.finditer(r"\btext\s*=\s*True\b", src)]
    assert not bare, (f"{rel}: bare text=True decodes with the locale codec "
                      f"(cp1252 on Windows); use **platform_lib.text_kwargs()")


def test_classify_sends_prompt_via_stdin_when_claude_is_cmd_shim(monkeypatch):
    import subprocess
    import otter_classify
    import platform_lib
    monkeypatch.setattr(otter_classify.profile_lib, "harness", lambda root=None: "claude")
    monkeypatch.setattr(platform_lib, "resolve_claude", lambda path=None: r"C:\npm\claude.cmd")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout='{"result": "general"}', stderr="")
    monkeypatch.setattr(otter_classify.subprocess, "run", fake_run)
    assert otter_classify.classify_domain("Weekly sync", "A: hi\nB: \u201chello\u201d") == "general"
    assert seen["kw"]["encoding"] == "utf-8"
    assert "Weekly sync" in seen["kw"]["input"]
    assert not any("Weekly sync" in str(a) for a in seen["cmd"])
