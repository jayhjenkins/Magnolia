"""output_format: task field, phrase detection, worker default, dispatch injection."""
import re
import sys

import pytest
import task_lib


def test_create_task_sets_explicit_format(tasks_root):
    tid, _ = task_lib.create_task("Q4 swim lanes", queue="agent", output_format="html")
    assert task_lib.read_task(tid)["frontmatter"]["output_format"] == "html"


def test_create_task_detects_phrase(tasks_root):
    tid, _ = task_lib.create_task("Pre-read for Thursday", queue="agent",
                                  description="Build it as HTML")
    assert task_lib.read_task(tid)["frontmatter"]["output_format"] == "html"


def test_create_task_explicit_md_overrides_phrase(tasks_root):
    tid, _ = task_lib.create_task("Pre-read", queue="agent",
                                  description="Build it as HTML", output_format="md")
    assert task_lib.read_task(tid)["frontmatter"]["output_format"] == "md"


def test_create_task_agent_creator_skips_detection(tasks_root):
    tid, _ = task_lib.create_task("Pre-read", queue="agent", creator="agent",
                                  description="Build it as HTML")
    assert "output_format" not in task_lib.read_task(tid)["frontmatter"]


def test_create_task_agent_creator_explicit_format_honored(tasks_root):
    tid, _ = task_lib.create_task("Pre-read", queue="agent", creator="agent",
                                  output_format="html")
    assert task_lib.read_task(tid)["frontmatter"]["output_format"] == "html"


def test_create_task_empty_format_is_none(tasks_root):
    tid, _ = task_lib.create_task("Strategy memo", queue="agent", output_format="")
    assert "output_format" not in task_lib.read_task(tid)["frontmatter"]


def test_create_task_defaults_absent(tasks_root):
    tid, _ = task_lib.create_task("Strategy memo", queue="agent")
    assert "output_format" not in task_lib.read_task(tid)["frontmatter"]


def test_create_task_rejects_bad_format(tasks_root):
    with pytest.raises(ValueError):
        task_lib.create_task("x", queue="agent", output_format="pdf")


def test_dispatch_appends_block_for_html():
    import task_dispatch, html_artifact_lib
    p = task_dispatch.apply_output_format("BASE", {"output_format": "html"}, {})
    assert p.startswith("BASE") and html_artifact_lib.dispatch_block() in p
    assert task_dispatch.apply_output_format("BASE", {}, {}) == "BASE"
    assert html_artifact_lib.dispatch_block() in task_dispatch.apply_output_format(
        "BASE", {}, {"output_format": "html"})


def test_dispatch_task_field_md_beats_worker_html():
    import task_dispatch
    assert task_dispatch.apply_output_format(
        "BASE", {"output_format": "md"}, {"output_format": "html"}) == "BASE"


def test_cli_add_format_lands_in_frontmatter(tasks_root, monkeypatch, capsys):
    import task_cli
    monkeypatch.delenv("TASK_AUTO_DISPATCH", raising=False)
    monkeypatch.setattr(sys, "argv", ["task", "add", "Swim lanes", "-q", "agent",
                                      "--format", "html"])
    task_cli.main()
    tid = re.search(r"TASK-\d+", capsys.readouterr().out).group(0)
    assert task_lib.read_task(tid)["frontmatter"]["output_format"] == "html"


def test_task_frontmatter_reads_disk_over_projection(tasks_root, monkeypatch):
    import task_dispatch
    monkeypatch.setattr(task_dispatch, "log", lambda *a, **k: None)
    tid, _ = task_lib.create_task("Swim lanes", queue="agent", output_format="html")
    fm = task_dispatch._task_frontmatter({"id": tid, "title": "Swim lanes"})
    assert fm["output_format"] == "html"
    assert task_dispatch._task_frontmatter({"id": "TASK-9999"}) == {"id": "TASK-9999"}


# --- dispatch_task call sites: capture the prompt handed to the harness ---

class _Launched(Exception):
    pass


def _capture_dispatch(monkeypatch, task, worker):
    import task_dispatch
    seen = {}

    def fake_cmd(prompt, *a, **k):
        seen["prompt"] = prompt
        raise _Launched()

    monkeypatch.setattr(task_dispatch, "log", lambda *a, **k: None)
    monkeypatch.setattr(task_dispatch, "build_claude_cmd", fake_cmd)
    monkeypatch.setattr(task_dispatch, "build_prompt_for_worker",
                        lambda tid, w, rerun=False: "WORKER PROMPT")
    monkeypatch.setattr(task_dispatch, "build_prompt",
                        lambda tid, rerun=False: "LEGACY PROMPT")
    workers = None
    if worker is not None:
        workers = [worker]
        monkeypatch.setattr(task_dispatch, "match_worker",
                            lambda t, ws: (worker, 1, ["test"]))
    with pytest.raises(_Launched):
        task_dispatch.dispatch_task(task, workers=workers)
    return seen["prompt"]


_WORKER = {"name": "w", "prompt_body": "do it", "allowed_tools": ["Read"], "max_turns": 5}


@pytest.mark.parametrize("worker,base", [(_WORKER, "WORKER PROMPT"), (None, "LEGACY PROMPT")])
def test_dispatch_call_site_html(tasks_root, monkeypatch, worker, base):
    import html_artifact_lib
    tid, _ = task_lib.create_task("Swim lanes", queue="agent", output_format="html")
    # Projection like `task list --json`: no output_format key.
    p = _capture_dispatch(monkeypatch, {"id": tid, "title": "Swim lanes"}, worker)
    assert p == base + html_artifact_lib.dispatch_block()
    assert p.endswith(html_artifact_lib.dispatch_block())


@pytest.mark.parametrize("worker,base", [(_WORKER, "WORKER PROMPT"), (None, "LEGACY PROMPT")])
def test_dispatch_call_site_md(tasks_root, monkeypatch, worker, base):
    tid, _ = task_lib.create_task("Strategy memo", queue="agent")
    p = _capture_dispatch(monkeypatch, {"id": tid, "title": "Strategy memo"}, worker)
    assert p == base


def test_task_frontmatter_fallback_logs_warn(monkeypatch):
    import task_dispatch
    lines = []
    monkeypatch.setattr(task_dispatch, "log", lambda msg, **k: lines.append(msg))
    task_dispatch._task_frontmatter({"id": "TASK-9999"})
    assert any(l.startswith("WARN") for l in lines)


def test_dispatch_rerun_of_existing_html_output_gets_block():
    import task_dispatch, html_artifact_lib
    fm = {"agent_output": "datasets/product/agent-output/x.html"}
    assert task_dispatch.apply_output_format("BASE", fm, {}).endswith(html_artifact_lib.dispatch_block())
    # an explicit md on the task still wins
    assert task_dispatch.apply_output_format("BASE", {**fm, "output_format": "md"}, {}) == "BASE"
    # an existing md output stays md
    assert task_dispatch.apply_output_format("BASE", {"agent_output": "x.md"}, {}) == "BASE"
