"""Tests for sentinel_runner.run_sentinel - the cron -> claude -p dispatch harness.

The runner loads a sentinel def, resolves scope (active programs + their Intent),
builds a prompt, dispatches the LLM (mocked here), parses the returned observation
records defensively, and appends them via program_lib.append_observation. The LLM
NEVER writes files - the runner does, through the validated writer. A bad record
or a bad run never raises.

Isolation: program_lib is pinned to a tmp dir (via `root` plus belt-and-suspenders
monkeypatching of _program_dir/_counter_path), so the real datasets/ is untouched.
The dispatch seam (_dispatch) and the adapter-config seam (_adapter_configured) are
monkeypatched so no real claude subprocess or adapter is ever spawned.
"""  # noqa
import json

import pytest

import program_lib
import sentinel_runner


def _pin_programs(tmp_path, monkeypatch):
    """Confine program_lib to a tmp datasets/programs dir."""
    pdir = tmp_path / "datasets" / "programs"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(program_lib, "_program_dir", lambda root=None: str(pdir))
    monkeypatch.setattr(
        program_lib, "_counter_path", lambda root=None: str(pdir / "_counter"))


def _seed_two_programs(tmp_path, monkeypatch):
    """Seed two active programs and return their ids."""
    _pin_programs(tmp_path, monkeypatch)
    pid1, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product",
        intent="Ship the alpha discovery spike.", root=str(tmp_path))
    pid2, _ = program_lib.create_program(
        type="roadmap-initiative", title="Beta", owner_role="product",
        intent="Beta launch readiness.", root=str(tmp_path))
    return pid1, pid2


def test_wellformed_json_appends_to_cited_programs(tmp_path, monkeypatch):
    pid1, pid2 = _seed_two_programs(tmp_path, monkeypatch)
    records = [
        {"program_id": pid1, "kind": "status-signal",
         "source": "datasets/meetings/a.md (#Summary)",
         "claim": "Alpha discovery spike reported complete.", "confidence": 0.9},
        {"program_id": pid2, "kind": "completion",
         "source": "datasets/meetings/b.md (#Action Items)",
         "claim": "Beta launch readiness sign-off recorded.", "confidence": 0.8},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch", lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("movement-watch", root=str(tmp_path))

    assert summary == {"sentinel": "movement-watch", "appended": 2, "dropped": 0}
    # The observations actually landed on the cited programs.
    body1 = program_lib.read_program(pid1, root=str(tmp_path))["body"]
    body2 = program_lib.read_program(pid2, root=str(tmp_path))["body"]
    assert "Alpha discovery spike reported complete." in body1
    assert "[status-signal]" in body1
    assert "Beta launch readiness sign-off recorded." in body2
    assert "[completion]" in body2


def test_run_sentinel_dispatches_at_the_defs_model_tier(tmp_path, monkeypatch):
    # movement-watch declares model_tier: deep; the runner must pass it through to
    # _dispatch so an interpretive sentinel gets a deeper model than a mechanical one.
    _seed_two_programs(tmp_path, monkeypatch)
    seen = {}
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: seen.setdefault("tier", tier) or "[]")
    sentinel_runner.run_sentinel("movement-watch", root=str(tmp_path))
    assert seen["tier"] == "deep"


def test_malformed_json_appends_zero_no_raise(tmp_path, monkeypatch):
    pid1, _ = _seed_two_programs(tmp_path, monkeypatch)
    monkeypatch.setattr(sentinel_runner, "_dispatch", lambda prompt, tier=None: "not json at all {{{")

    summary = sentinel_runner.run_sentinel("movement-watch", root=str(tmp_path))

    assert summary["appended"] == 0
    # Program untouched (no observation entries).
    body1 = program_lib.read_program(pid1, root=str(tmp_path))["body"]
    assert "sentinel:movement-watch" not in body1


def test_unknown_program_id_dropped(tmp_path, monkeypatch):
    pid1, _ = _seed_two_programs(tmp_path, monkeypatch)
    records = [
        {"program_id": "PROG-9999", "kind": "status-signal",
         "source": "datasets/meetings/a.md", "claim": "Belongs to nobody.",
         "confidence": 0.9},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch", lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("movement-watch", root=str(tmp_path))

    assert summary["appended"] == 0
    assert summary["dropped"] == 1
    body1 = program_lib.read_program(pid1, root=str(tmp_path))["body"]
    assert "Belongs to nobody." not in body1


def test_null_program_id_dropped(tmp_path, monkeypatch):
    _seed_two_programs(tmp_path, monkeypatch)
    records = [
        {"program_id": None, "kind": "status-signal",
         "source": "datasets/meetings/a.md", "claim": "Unattributed.", "confidence": 0.5},
        {"program_id": "", "kind": "risk",
         "source": "datasets/meetings/a.md", "claim": "Also unattributed.", "confidence": 0.5},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch", lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("movement-watch", root=str(tmp_path))

    assert summary["appended"] == 0
    assert summary["dropped"] == 2


def test_rejected_record_counted_as_dropped_no_crash(tmp_path, monkeypatch):
    pid1, _ = _seed_two_programs(tmp_path, monkeypatch)
    records = [
        # Bad kind -> append_observation raises ValueError -> counted as dropped.
        {"program_id": pid1, "kind": "vibes",
         "source": "datasets/meetings/a.md", "claim": "Bad kind record.", "confidence": 0.9},
        # A valid one alongside it still lands.
        {"program_id": pid1, "kind": "completion",
         "source": "datasets/meetings/a.md", "claim": "Good record.", "confidence": 0.9},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch", lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("movement-watch", root=str(tmp_path))

    assert summary["appended"] == 1
    assert summary["dropped"] == 1
    body1 = program_lib.read_program(pid1, root=str(tmp_path))["body"]
    assert "Good record." in body1
    assert "Bad kind record." not in body1


def test_tracker_truth_unconfigured_adapter_is_clean_noop(tmp_path, monkeypatch):
    pid1, _ = _seed_two_programs(tmp_path, monkeypatch)
    # The adapter check reports unconfigured; dispatch must NOT be called.
    monkeypatch.setattr(sentinel_runner, "_adapter_configured", lambda name, root=None: False)

    def _boom(prompt, tier=None):
        raise AssertionError("dispatch must not run when the adapter is unconfigured")

    monkeypatch.setattr(sentinel_runner, "_dispatch", _boom)

    summary = sentinel_runner.run_sentinel("tracker-truth", root=str(tmp_path))

    assert summary == {"sentinel": "tracker-truth", "appended": 0, "dropped": 0}
    body1 = program_lib.read_program(pid1, root=str(tmp_path))["body"]
    assert "sentinel:tracker-truth" not in body1


def _seed_two_programs_with_epics(tmp_path, monkeypatch, epic1, epic2):
    """Seed two active programs, each carrying a links.tracker_epic reference."""
    _pin_programs(tmp_path, monkeypatch)
    pid1, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product",
        intent="Ship the alpha discovery spike.",
        frontmatter_extra={"links": {"tracker_epic": epic1}}, root=str(tmp_path))
    pid2, _ = program_lib.create_program(
        type="roadmap-initiative", title="Beta", owner_role="product",
        intent="Beta launch readiness.",
        frontmatter_extra={"links": {"tracker_epic": epic2}}, root=str(tmp_path))
    return pid1, pid2


def test_tracker_truth_configured_adapter_is_mechanical_no_dispatch(tmp_path, monkeypatch):
    # A configured tracker-truth must NOT call the LLM. It reads adapter facts and
    # maps them deterministically: done/closed -> completion, otherwise status-signal.
    pid1, pid2 = _seed_two_programs_with_epics(tmp_path, monkeypatch, "EPIC-1", "EPIC-2")

    monkeypatch.setattr(sentinel_runner, "_adapter_configured", lambda name, root=None: True)
    facts = {
        "EPIC-1": {"status": "Done", "title": "Alpha epic", "due": "2026-09-15"},
        "EPIC-2": {"status": "In Progress", "title": "Beta epic", "due": "2026-10-01"},
    }
    monkeypatch.setattr(
        sentinel_runner.adapters, "fetch_status",
        lambda family, issue_key, root=None: facts.get(issue_key))

    def _boom(prompt, tier=None):
        raise AssertionError("tracker-truth is mechanical - it must NOT dispatch the LLM")
    monkeypatch.setattr(sentinel_runner, "_dispatch", _boom)

    summary = sentinel_runner.run_sentinel("tracker-truth", root=str(tmp_path))

    assert summary["appended"] == 2
    body1 = program_lib.read_program(pid1, root=str(tmp_path))["body"]
    body2 = program_lib.read_program(pid2, root=str(tmp_path))["body"]
    # EPIC-1 Done -> completion; EPIC-2 open -> status-signal.
    assert "[completion]" in body1
    assert "adapter:project_management:EPIC-1" in body1
    assert "[status-signal]" in body2
    assert "adapter:project_management:EPIC-2" in body2


def test_tracker_truth_skips_programs_with_no_epic(tmp_path, monkeypatch):
    # A program with no links.tracker_epic is skipped (fetch_status never called for it).
    _pin_programs(tmp_path, monkeypatch)
    pid1, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product",
        intent="Has an epic.",
        frontmatter_extra={"links": {"tracker_epic": "EPIC-1"}}, root=str(tmp_path))
    pid2, _ = program_lib.create_program(
        type="roadmap-initiative", title="Beta", owner_role="product",
        intent="No epic linked.", root=str(tmp_path))

    monkeypatch.setattr(sentinel_runner, "_adapter_configured", lambda name, root=None: True)
    monkeypatch.setattr(
        sentinel_runner.adapters, "fetch_status",
        lambda family, issue_key, root=None:
            {"status": "Closed", "title": "x", "due": None} if issue_key == "EPIC-1" else None)
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no dispatch")))

    summary = sentinel_runner.run_sentinel("tracker-truth", root=str(tmp_path))

    assert summary["appended"] == 1
    body2 = program_lib.read_program(pid2, root=str(tmp_path))["body"]
    assert "sentinel:tracker-truth" not in body2


def test_tracker_truth_survives_provider_raising(tmp_path, monkeypatch):
    # One epic whose adapter read raises must not abort the whole run: that program
    # is skipped, the other still lands. A sentinel never crashes a run.
    pid1, pid2 = _seed_two_programs_with_epics(tmp_path, monkeypatch, "EPIC-1", "EPIC-2")
    monkeypatch.setattr(sentinel_runner, "_adapter_configured", lambda name, root=None: True)

    def _fetch(family, issue_key, root=None):
        if issue_key == "EPIC-1":
            raise RuntimeError("transient adapter blowup")
        return {"status": "Done", "title": "Beta epic", "due": None}
    monkeypatch.setattr(sentinel_runner.adapters, "fetch_status", _fetch)
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no dispatch")))

    summary = sentinel_runner.run_sentinel("tracker-truth", root=str(tmp_path))

    assert summary["appended"] == 1  # EPIC-1 skipped, EPIC-2 landed
    assert "[completion]" in program_lib.read_program(pid2, root=str(tmp_path))["body"]


def test_tracker_truth_unconfigured_via_fetch_status_none(tmp_path, monkeypatch):
    # fetch_status returns None for every epic (adapter unconfigured / off) ->
    # 0 appended, clean, and _dispatch is NEVER called.
    pid1, _ = _seed_two_programs_with_epics(tmp_path, monkeypatch, "EPIC-1", "EPIC-2")
    monkeypatch.setattr(sentinel_runner, "_adapter_configured", lambda name, root=None: False)
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no dispatch")))

    summary = sentinel_runner.run_sentinel("tracker-truth", root=str(tmp_path))

    assert summary == {"sentinel": "tracker-truth", "appended": 0, "dropped": 0}
    body1 = program_lib.read_program(pid1, root=str(tmp_path))["body"]
    assert "sentinel:tracker-truth" not in body1
