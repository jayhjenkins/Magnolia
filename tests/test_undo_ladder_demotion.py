"""A ladder-demotion receipt's Undo restores the tier the type was demoted
from. Unlike an autoship receipt (which cannot un-send an external action),
a demotion is purely a local ladder.json tier flip, so it CAN be reverted —
undo_receipt must call ladder_lib.set_tier back to demote_from_tier and mark
the card done, with no git-revert attempted.

Fixture mirrors tests/test_undo_autoship.py: ladder_lib helpers pinned to a
temp ladder.json, subprocess.run spied to prove no git call happens.
"""
import pytest


@pytest.fixture
def srv(tasks_root, tmp_path, monkeypatch):
    import task_server, ladder_lib, task_lib

    ladder_path = str(tmp_path / "ladder.json")

    def _wrap_path(orig):
        def wrapper(*a, **k):
            if "path" not in k:
                k = {**k, "path": ladder_path}
            return orig(*a, **k)
        return wrapper

    for fn in ("set_tier", "tier_of"):
        monkeypatch.setattr(ladder_lib, fn, _wrap_path(getattr(ladder_lib, fn)))

    git_calls = []
    real_run = task_server.subprocess.run

    def _spy_run(cmd, *a, **k):
        git_calls.append(cmd)
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(task_server.subprocess, "run", _spy_run)

    return task_server, ladder_lib, task_lib, ladder_path, git_calls


def _make_demotion_receipt(task_lib, task_type="prd-draft", from_tier="supervised", to_tier="shadow"):
    cid, _ = task_lib.create_task(
        f"Demoted '{task_type}': {from_tier} -> {to_tier}", queue="collab", domain="ops",
        creator="agent", description="ladder-demotion receipt", card_type="receipt")
    task_lib.update_task(cid, changes={
        "receipt_kind": "ladder-demotion",
        "receipt_summary": f"Demoted '{task_type}': {from_tier} -> {to_tier}",
        "demote_task_type": task_type,
        "demote_from_tier": from_tier,
        "demote_to_tier": to_tier,
    })
    return cid


def test_undo_ladder_demotion_restores_tier(srv):
    task_server, ladder_lib, task_lib, _, git_calls = srv
    cid = _make_demotion_receipt(task_lib)
    ladder_lib.set_tier("prd-draft", "shadow")  # the state after the demotion already happened

    task_server.undo_receipt(cid)

    assert ladder_lib.tier_of("prd-draft") == "supervised"
    assert all("revert" not in c for c in git_calls), git_calls
    fm = task_lib.read_task(cid)["frontmatter"]
    assert fm["status"] == "done"


def test_undo_ladder_demotion_no_git_revert(srv):
    task_server, ladder_lib, task_lib, _, git_calls = srv
    cid = _make_demotion_receipt(task_lib)
    ladder_lib.set_tier("prd-draft", "shadow")

    task_server.undo_receipt(cid)

    assert not any(
        isinstance(c, (list, tuple)) and "git" in c and "revert" in c
        for c in git_calls
    ), git_calls


def test_undo_ladder_demotion_missing_fields_stays_honest(srv):
    """A demotion receipt MISSING demote_task_type/demote_from_tier (defensive/
    unreachable): undo must NOT touch the ladder, NOT git-revert, and mark the
    card done without claiming it restored anything."""
    task_server, ladder_lib, task_lib, _, git_calls = srv
    cid, _ = task_lib.create_task(
        "Demoted 'prd-draft': supervised -> shadow", queue="collab", domain="ops",
        creator="agent", description="ladder-demotion receipt", card_type="receipt")
    task_lib.update_task(cid, changes={
        "receipt_kind": "ladder-demotion",
        "receipt_summary": "Demoted 'prd-draft': supervised -> shadow",
    })

    tier_changes = []
    real_set_tier = ladder_lib.set_tier

    def _spy_set_tier(*a, **k):
        tier_changes.append((a, k))
        return real_set_tier(*a, **k)

    import unittest.mock as _mock
    with _mock.patch.object(ladder_lib, "set_tier", _spy_set_tier):
        task_server.undo_receipt(cid)

    assert tier_changes == [], tier_changes
    assert all("revert" not in c for c in git_calls), git_calls
    read = task_lib.read_task(cid)
    assert read["frontmatter"]["status"] == "done"
    assert "nothing was restored" in read["body"], read["body"]
