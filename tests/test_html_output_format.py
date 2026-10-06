"""output_format: task field, phrase detection, worker default, dispatch injection."""
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
    tid = capsys.readouterr().out.split()[1]
    assert task_lib.read_task(tid)["frontmatter"]["output_format"] == "html"


def test_task_frontmatter_reads_disk_over_projection(tasks_root):
    import task_dispatch
    tid, _ = task_lib.create_task("Swim lanes", queue="agent", output_format="html")
    fm = task_dispatch._task_frontmatter({"id": tid, "title": "Swim lanes"})
    assert fm["output_format"] == "html"
    assert task_dispatch._task_frontmatter({"id": "TASK-9999"}) == {"id": "TASK-9999"}
