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


def test_sheet_watch_blind_when_locator_unconfigured(tmp_path, monkeypatch):
    """A sheet sentinel with no configured locator is BLIND, not live: it does NOT
    dispatch and records success=False so the silent-archive door never reads a
    dormant EOS program off it. inc5 slice 10."""
    _seed_two_programs(tmp_path, monkeypatch)
    monkeypatch.setattr(sentinel_runner, "_sheet_configured", lambda name, root=None: False)
    called = []
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda *a, **k: called.append(1) or "[]")
    summary = sentinel_runner.run_sentinel("sheet-watch", root=str(tmp_path))
    assert summary["appended"] == 0
    assert "error" in summary          # blind
    assert not called                  # never dispatched (can't read the sheet)
    runs = sentinel_runner.read_sentinel_runs(root=str(tmp_path))
    assert runs["sheet-watch"].get("last_error")
    assert "last_success" not in runs["sheet-watch"]   # blind != succeeded


def test_sheet_watch_configured_dispatches_and_records_live(tmp_path, monkeypatch):
    pid1, _ = _seed_two_programs(tmp_path, monkeypatch)
    monkeypatch.setattr(sentinel_runner, "_sheet_configured", lambda name, root=None: True)
    records = [{"program_id": pid1, "kind": "status-signal",
                "source": "sheet:EOS/Scorecard!A2", "claim": "Rock on track.",
                "confidence": 0.8}]
    called = []
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: called.append(1) or json.dumps(records))
    summary = sentinel_runner.run_sentinel("sheet-watch", root=str(tmp_path))
    assert called                       # configured -> live dispatch
    assert summary["appended"] == 1
    runs = sentinel_runner.read_sentinel_runs(root=str(tmp_path))
    assert runs["sheet-watch"]["last_success"]   # live + succeeded


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


# ── The program-intake sentinel (the birth-path routing branch) ───────────────
#
# program-intake is the intake sentinel: it returns ROUTING records the runner
# applies deterministically by route (observe/capture/candidate/ignore). Same
# fence as movement-watch: the LLM never writes; bad records are dropped and
# counted, never raised. The intake (program-intake type) program is the
# candidate nursery; candidate routes upsert into it.


def _seed_intake_and_active(tmp_path, monkeypatch):
    """Seed the intake nursery plus one active (non-intake) program.

    Returns (intake_id, active_id).
    """
    _pin_programs(tmp_path, monkeypatch)
    intake_id, _ = program_lib.create_program(
        type="program-intake", title="Program intake", owner_role="product",
        intent="The nursery.", frontmatter_extra={"items": []}, root=str(tmp_path))
    active_id, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product",
        intent="Ship the alpha discovery spike.", root=str(tmp_path))
    return intake_id, active_id


def test_intake_observe_route_appends_observation(tmp_path, monkeypatch):
    intake_id, active_id = _seed_intake_and_active(tmp_path, monkeypatch)
    records = [
        {"route": "observe", "program_id": active_id, "kind": "status-signal",
         "source": "datasets/meetings/a.md (#Summary)",
         "claim": "Alpha spike progressing.", "confidence": 0.9},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("program-intake", root=str(tmp_path))

    assert summary["appended"] == 1
    assert summary["dropped"] == 0
    body = program_lib.read_program(active_id, root=str(tmp_path))["body"]
    assert "Alpha spike progressing." in body
    assert "[status-signal]" in body


def test_intake_capture_route_appends_capture_observation(tmp_path, monkeypatch):
    intake_id, active_id = _seed_intake_and_active(tmp_path, monkeypatch)
    records = [
        {"route": "capture", "program_id": active_id,
         "source": "datasets/meetings/b.md (#Action Items)",
         "claim": "New inbox item for the cycle.", "confidence": 0.7},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("program-intake", root=str(tmp_path))

    assert summary["appended"] == 1
    body = program_lib.read_program(active_id, root=str(tmp_path))["body"]
    assert "New inbox item for the cycle." in body
    assert "[capture]" in body


def test_intake_candidate_route_upserts_into_nursery(tmp_path, monkeypatch):
    intake_id, active_id = _seed_intake_and_active(tmp_path, monkeypatch)
    records = [
        {"route": "candidate", "program_type": "roadmap-initiative",
         "title": "Smart reconciliation", "anchor": "EPIC-77",
         "source": "datasets/meetings/c.md (#Discussion)",
         "claim": "Repeated ask for smart reconciliation.", "confidence": 0.6},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("program-intake", root=str(tmp_path))

    assert summary["appended"] == 1
    assert summary["dropped"] == 0
    intake_fm = program_lib.read_program(intake_id, root=str(tmp_path))["frontmatter"]
    items = intake_fm.get("items") or []
    assert len(items) == 1
    cand = items[0]
    assert cand["title"] == "Smart reconciliation"
    assert cand["program_type"] == "roadmap-initiative"
    assert cand["anchor"] == "EPIC-77"
    assert cand["status"] == "open"


def test_intake_candidate_declared_flows_to_nursery(tmp_path, monkeypatch):
    intake_id, active_id = _seed_intake_and_active(tmp_path, monkeypatch)
    records = [
        {"route": "candidate", "program_type": "roadmap-initiative",
         "title": "Q3 platform rock", "declared": True,
         "source": "datasets/meetings/c.md (#Discussion)",
         "claim": "Leadership declared this as a Q3 rock."},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("program-intake", root=str(tmp_path))

    assert summary["appended"] == 1
    items = program_lib.read_program(intake_id, root=str(tmp_path))["frontmatter"]["items"]
    assert items[0]["declared"] is True


def test_intake_candidate_without_declared_is_false(tmp_path, monkeypatch):
    intake_id, active_id = _seed_intake_and_active(tmp_path, monkeypatch)
    records = [
        {"route": "candidate", "program_type": "roadmap-initiative",
         "title": "Some initiative",
         "source": "datasets/meetings/c.md (#Discussion)",
         "claim": "Mentioned in passing."},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("program-intake", root=str(tmp_path))

    assert summary["appended"] == 1
    items = program_lib.read_program(intake_id, root=str(tmp_path))["frontmatter"]["items"]
    assert items[0].get("declared", False) is False


def test_intake_candidate_non_bool_declared_does_not_raise(tmp_path, monkeypatch):
    intake_id, active_id = _seed_intake_and_active(tmp_path, monkeypatch)
    records = [
        {"route": "candidate", "program_type": "roadmap-initiative",
         "title": "Some initiative", "declared": "yes",
         "source": "datasets/meetings/c.md (#Discussion)",
         "claim": "Garbled declared field."},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: json.dumps(records))

    # Must not raise; the candidate lands with a coerced bool declared.
    summary = sentinel_runner.run_sentinel("program-intake", root=str(tmp_path))

    assert summary["appended"] == 1
    items = program_lib.read_program(intake_id, root=str(tmp_path))["frontmatter"]["items"]
    assert isinstance(items[0].get("declared", False), bool)
    assert items[0]["declared"] is True  # non-empty string -> truthy -> True


def test_intake_ignore_route_is_noop(tmp_path, monkeypatch):
    intake_id, active_id = _seed_intake_and_active(tmp_path, monkeypatch)
    records = [
        {"route": "ignore", "source": "datasets/meetings/d.md",
         "claim": "Not cadence-level chatter."},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("program-intake", root=str(tmp_path))

    assert summary == {"sentinel": "program-intake", "appended": 0, "dropped": 0}
    intake_fm = program_lib.read_program(intake_id, root=str(tmp_path))["frontmatter"]
    assert (intake_fm.get("items") or []) == []
    body = program_lib.read_program(active_id, root=str(tmp_path))["body"]
    assert "sentinel:program-intake" not in body


def test_intake_candidate_dropped_when_no_intake_program(tmp_path, monkeypatch):
    # No program-intake program exists -> a candidate route is DROPPED, not raised.
    _pin_programs(tmp_path, monkeypatch)
    active_id, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product",
        intent="Has no nursery to land a candidate in.", root=str(tmp_path))
    records = [
        {"route": "candidate", "program_type": "roadmap-initiative",
         "title": "Orphan candidate",
         "source": "datasets/meetings/e.md", "claim": "Program-worthy ask."},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("program-intake", root=str(tmp_path))

    assert summary["appended"] == 0
    assert summary["dropped"] == 1


def test_intake_observe_unknown_program_dropped(tmp_path, monkeypatch):
    intake_id, active_id = _seed_intake_and_active(tmp_path, monkeypatch)
    records = [
        {"route": "observe", "program_id": "PROG-9999", "kind": "status-signal",
         "source": "datasets/meetings/f.md", "claim": "Belongs to nobody."},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: json.dumps(records))

    summary = sentinel_runner.run_sentinel("program-intake", root=str(tmp_path))

    assert summary["appended"] == 0
    assert summary["dropped"] == 1


def test_program_intake_definition_passes_validate_sentinel():
    import sentinel_lib
    definition = sentinel_lib.load_sentinel("program-intake")
    assert sentinel_lib.validate_sentinel(definition) == []


# ── Blind-Sentinel Telemetry (Task 5) ──────────────────────────────────────
#
# Telemetry tracking for sentinels: record when a sentinel last ran, whether it
# succeeded, how many items it emitted, and any errors. Detect blind sentinels
# (hasn't run recently, or has errors).


def test_record_and_read_sentinel_run_roundtrip(tmp_path, monkeypatch):
    """Record a successful sentinel run and read it back."""
    root = str(tmp_path)

    # Record a successful run with 3 emitted items.
    sentinel_runner.record_sentinel_run(
        "movement-watch", success=True, emitted_count=3, root=root)

    # Read back the telemetry.
    telemetry = sentinel_runner.read_sentinel_runs(root)

    # Assert the sentinel's entry exists and has the right shape.
    assert "movement-watch" in telemetry
    entry = telemetry["movement-watch"]
    assert "last_run" in entry
    assert "last_success" in entry
    assert entry["last_success"] == entry["last_run"]  # success=True
    assert entry["last_emitted_count"] == 3
    assert entry.get("last_error") is None


def test_record_sentinel_run_error_sets_last_error_keeps_last_success(tmp_path, monkeypatch):
    """Error run updates last_error and last_run, but preserves last_success."""
    root = str(tmp_path)

    # Record a successful run with an explicit timestamp.
    first_now = "2026-06-18T18:00:00Z"
    sentinel_runner.record_sentinel_run(
        "movement-watch", success=True, emitted_count=0, root=root, now=first_now)
    telemetry = sentinel_runner.read_sentinel_runs(root)
    first_success = telemetry["movement-watch"]["last_success"]
    assert first_success == first_now

    # Record a failed run with a later timestamp.
    error_now = "2026-06-18T18:00:01Z"
    sentinel_runner.record_sentinel_run(
        "movement-watch", success=False, error="Connection failed",
        emitted_count=0, root=root, now=error_now)

    # Read back the telemetry.
    telemetry = sentinel_runner.read_sentinel_runs(root)
    entry = telemetry["movement-watch"]

    # Assert the error is recorded.
    assert entry["last_error"] == "Connection failed"
    # last_run was updated to the latest call.
    assert entry["last_run"] == error_now
    assert entry["last_run"] > first_success
    # last_success is unchanged (from the first successful run).
    assert entry["last_success"] == first_success
    # last_emitted_count from the latest call.
    assert entry["last_emitted_count"] == 0


def test_run_sentinel_stamps_telemetry(tmp_path, monkeypatch):
    """run_sentinel wraps the implementation and records telemetry."""
    root = str(tmp_path)

    # Seed a simple program so the sentinel has something to work with.
    _pin_programs(tmp_path, monkeypatch)
    pid1, _ = program_lib.create_program(
        type="roadmap-initiative", title="Alpha", owner_role="product",
        intent="Test.", root=root)

    # Mock dispatch to return one observation.
    records = [
        {"program_id": pid1, "kind": "status-signal",
         "source": "test.md", "claim": "Test observation.", "confidence": 0.9},
    ]
    monkeypatch.setattr(sentinel_runner, "_dispatch",
                        lambda prompt, tier=None: json.dumps(records))

    # Run the sentinel.
    summary = sentinel_runner.run_sentinel("movement-watch", root=root)

    # Assert the sentinel succeeded and recorded 1 observation.
    assert summary["appended"] == 1

    # Check that telemetry was stamped.
    telemetry = sentinel_runner.read_sentinel_runs(root)
    assert "movement-watch" in telemetry
    entry = telemetry["movement-watch"]
    assert "last_run" in entry
    assert entry["last_success"] == entry["last_run"]  # success
    assert entry["last_emitted_count"] == 1


def test_run_sentinel_records_failure_when_def_unloadable(tmp_path, monkeypatch):
    """A sentinel whose def will not load is BLIND: telemetry records a failed run
    (last_error set, no last_success) so the silent-archive door does not treat it
    as live."""
    root = str(tmp_path)
    _pin_programs(tmp_path, monkeypatch)
    # A name with no def under scripts/sentinels/ -> load_sentinel raises -> the
    # impl returns a summary carrying `error`.
    summary = sentinel_runner.run_sentinel("no-such-sentinel-xyz", root=root)
    assert "error" in summary
    entry = sentinel_runner.read_sentinel_runs(root)["no-such-sentinel-xyz"]
    assert entry.get("last_error")           # recorded as blind
    assert entry.get("last_success") is None  # NOT a success
    assert "last_run" in entry                # but we know it was attempted


def test_run_sentinel_passes_date_not_timestamp_to_impl(tmp_path, monkeypatch):
    """Regression: the wrapper must NOT pass a full ISO timestamp into the impl.

    The impl treats `now` as a YYYY-MM-DD date (observation date + scan window);
    the telemetry stamp is a separate full timestamp. If the wrapper conflated
    them, observation headers would be malformed in production. Lock that the impl
    receives `now` verbatim (None when the caller passed None) while telemetry
    still records a full timestamp.
    """
    root = str(tmp_path)
    _pin_programs(tmp_path, monkeypatch)

    seen = {}

    def fake_impl(name, root=None, now=None):
        seen["now"] = now
        return {"sentinel": name, "appended": 0, "dropped": 0}

    monkeypatch.setattr(sentinel_runner, "_run_sentinel_impl", fake_impl)

    sentinel_runner.run_sentinel("movement-watch", root=root)  # now defaults to None

    # The impl must see None (so its own [:10] date default applies), NOT a
    # fabricated full timestamp.
    assert seen["now"] is None
    # Telemetry still got a real timestamp (full ISO, longer than a date).
    entry = sentinel_runner.read_sentinel_runs(root)["movement-watch"]
    assert len(entry["last_run"]) > len("2026-06-18")
