"""Chat prompts tell the agent to edit an HTML output in place."""
import pytest

import chat_runner
import html_artifact_lib


@pytest.fixture(autouse=True)
def _profile(monkeypatch):
    monkeypatch.setattr(chat_runner.profile_lib, "display_name", lambda *a, **k: "Dana Cole")
    monkeypatch.setattr(chat_runner.profile_lib, "company", lambda *a, **k: "Acme")


def _task(out):
    return {"id": "TASK-0001", "title": "t", "agent_output": out}


HTML = "datasets/product/x.html"


def test_context_prompt_includes_hint_for_html():
    p = chat_runner.build_context_prompt(_task(HTML), "", "tweak it")
    assert html_artifact_lib.chat_hint(HTML) in p
    assert p.index("HTML page at") < p.index("tweak it")


def test_context_prompt_no_hint_for_md():
    p = chat_runner.build_context_prompt(_task("datasets/product/x.md"), "", "tweak it")
    assert "HTML page at" not in p


def test_context_prompt_no_hint_without_output():
    p = chat_runner.build_context_prompt(_task(None), "", "tweak it")
    assert "HTML page at" not in p


def test_resume_steady_state_includes_hint_for_html():
    p = chat_runner.build_resume_prompt(_task(HTML), "tweak it")
    assert html_artifact_lib.chat_hint(HTML) in p
    assert p.index("HTML page at") < p.index("tweak it")


def test_resume_handoff_includes_hint_for_html():
    p = chat_runner.build_resume_prompt(_task(HTML), "tweak it",
                                        first_interactive=True, body="b")
    assert html_artifact_lib.chat_hint(HTML) in p
    assert p.index("HTML page at") < p.index("tweak it")


def test_resume_no_hint_for_md():
    for kw in ({}, {"first_interactive": True, "body": "b"}):
        p = chat_runner.build_resume_prompt(_task("datasets/product/x.md"), "tweak it", **kw)
        assert "HTML page at" not in p
