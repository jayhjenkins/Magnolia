"""Parse / string checks for the priority-digest worker prose (Cadence inc3b, Task 5).

Deterministic assertions over scripts/workers/priority-digest.md and the dispatch
matcher. We assert FRONTMATTER shape + MATCH selection + that the prose carries the
load-bearing instructions (versioned write via the CLI, explicit slips, the doc is the
deliverable - no drafted send-message card). We NEVER assert on claude output -- the worker is prose.
"""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")
WORKER = os.path.join(SCRIPTS, "workers", "priority-digest.md")

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import task_dispatch  # noqa: E402


@pytest.fixture(scope="module")
def parsed():
    fm, body = task_dispatch._parse_worker_frontmatter(WORKER)
    assert fm is not None, "priority-digest.md frontmatter did not parse"
    return fm, body


def test_frontmatter_name_and_match(parsed):
    fm, _ = parsed
    assert fm["name"] == "priority-digest"
    assert fm["match"]["task_type"] == ["priority-digest"]


def test_frontmatter_tier_and_tools(parsed):
    fm, _ = parsed
    assert fm.get("tier"), "tier must be set"
    assert fm["tier"] == "deep"
    assert fm["allowed_tools"], "allowed_tools must be non-empty"
    assert fm.get("langfuse_prompt"), "langfuse_prompt token must be set"


def test_match_worker_selects_priority_digest():
    workers = task_dispatch.load_workers()
    names = {w.get("name") for w in workers}
    assert "priority-digest" in names, f"worker not loaded; have {names}"
    # Force the deterministic regex path (no claude CLI in tests) by stubbing the LLM matcher.
    import unittest.mock as mock
    with mock.patch.object(task_dispatch, "_match_worker_llm", return_value=(None, None)):
        worker, score, _ = task_dispatch.match_worker(
            {"task_type": "priority-digest", "title": "x", "description": "y", "domain": ""},
            workers,
        )
    assert worker.get("name") == "priority-digest"
    assert score >= 100  # exact task_type match scores +100


def test_body_instructs_versioned_artifact_via_cli(parsed):
    _, body = parsed
    # Artifacts go ONLY through the versioning CLI, never a raw Write to the artifacts dir.
    assert "program_lib.py write-artifact" in body


def test_body_flags_slips_explicitly(parsed):
    _, body = parsed
    low = body.lower()
    assert "slip" in low, "the prose must instruct flagging slips explicitly"


def test_body_does_not_draft_a_message(parsed):
    _, body = parsed
    # The doc is the deliverable; the operator writes their own message. No
    # send-message card, no ladder-gated draft banner.
    for token in ("send-message", "--message-channel", "--message-to", "tier_of"):
        assert token not in body, f"worker still drafts a send: {token!r}"


def test_body_completes_with_the_doc(parsed):
    _, body = parsed
    assert "agent:complete" in body and "--output" in body


def test_body_is_ascii_safe(parsed):
    _, body = parsed
    # ASCII-safe runtime guidance: no em dash / en dash / smart punctuation in the prose.
    for bad in ("—", "–", "‘", "’", "“", "”"):
        assert bad not in body, f"non-ASCII char {bad!r} found in worker body"
