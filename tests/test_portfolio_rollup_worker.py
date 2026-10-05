"""Parse / string checks for the portfolio-rollup worker prose (Cadence inc5, slice 11).

Deterministic assertions over scripts/workers/portfolio-rollup.md and the dispatch
matcher. We assert FRONTMATTER shape + MATCH selection + that the prose carries the
load-bearing instructions (read the WHOLE portfolio, name drifts, versioned write via
the CLI, the doc is the deliverable - no drafted send-message card). We NEVER assert
on claude output -- the worker is prose.
"""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")
WORKER = os.path.join(SCRIPTS, "workers", "portfolio-rollup.md")

if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import task_dispatch  # noqa: E402


@pytest.fixture(scope="module")
def parsed():
    fm, body = task_dispatch._parse_worker_frontmatter(WORKER)
    assert fm is not None, "portfolio-rollup.md frontmatter did not parse"
    return fm, body


def test_frontmatter_name_and_match(parsed):
    fm, _ = parsed
    assert fm["name"] == "portfolio-rollup"
    assert fm["match"]["task_type"] == ["portfolio-rollup"]


def test_frontmatter_tier_and_tools(parsed):
    fm, _ = parsed
    assert fm["tier"] == "deep"
    assert fm["allowed_tools"], "allowed_tools must be non-empty"
    assert fm.get("langfuse_prompt"), "langfuse_prompt token must be set"


def test_match_worker_selects_portfolio_rollup():
    workers = task_dispatch.load_workers()
    names = {w.get("name") for w in workers}
    assert "portfolio-rollup" in names, f"worker not loaded; have {names}"
    import unittest.mock as mock
    with mock.patch.object(task_dispatch, "_match_worker_llm", return_value=(None, None)):
        worker, score, _ = task_dispatch.match_worker(
            {"task_type": "portfolio-rollup", "title": "x", "description": "y", "domain": ""},
            workers,
        )
    assert worker.get("name") == "portfolio-rollup"
    assert score >= 100


def test_body_reads_whole_portfolio_and_synthesizes_themes(parsed):
    _, body = parsed
    low = body.lower()
    assert "status: active" in body          # enumerates the whole portfolio
    assert "family" in low and "theme" in low  # groups by family + cross-program themes


def test_body_names_drifts(parsed):
    _, body = parsed
    low = body.lower()
    assert "drift" in low and "broken" in low


def test_body_versioned_artifact_is_the_deliverable(parsed):
    _, body = parsed
    assert "program_lib.py write-artifact" in body
    assert "agent:complete" in body and "--output" in body


def test_body_does_not_draft_a_message(parsed):
    _, body = parsed
    # The doc is the deliverable; the operator writes their own message.
    for token in ("send-message", "--message-channel", "--message-to",
                  "--attachments", "tier_of"):
        assert token not in body, f"worker still drafts a send: {token!r}"


def test_body_is_ascii_safe(parsed):
    _, body = parsed
    for bad in ("—", "–", "‘", "’", "“", "”"):
        assert bad not in body, f"non-ASCII char {bad!r} found in worker body"


def test_body_forbids_draft_banner(parsed):
    _, body = parsed
    assert 'no "draft for review" banner' in body.lower()
