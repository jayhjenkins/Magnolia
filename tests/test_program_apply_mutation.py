"""Tests for program_lib.apply_mutation — the closed-set proposal applier.

When a human ACCEPTS a Cadence propose-update card, the program mutation rides
this function (Task 7). It is the human-side counterpart to the reconciler's
fact door (reconcile._maybe_advance_phase): both advance a phase + stamp
phase_entered through the SAME shared helper (program_lib._advance_phase_fm), so a
human-accepted advance and an auto-advance touch the file identically.

apply_mutation is Tier-1: a LOCAL program-file mutation only. No external write,
no git commit. Append-only (invariant #6): it appends a completion observation
and never deletes. ASCII-safe runtime strings (invariant #8).

Isolation: every test confines program_lib to a tmp dir via the `root` arg plus
belt-and-suspenders monkeypatching of `_program_dir`/`_counter_path`, so the real
`datasets/` is never touched.
"""

import pytest

import program_lib


def _seed_program(tmp_path, monkeypatch, *, phase="discovery",
                  phase_entered=None, checkpoints=None, scalar_entered=False):
    """Create an isolated roadmap-initiative program and return its id.

    The default shape mirrors the seed registry's roadmap-initiative pipeline:
    discovery -> planning -> execution -> shipped -> verified(terminal), with a
    `discovery-exit` checkpoint guarding discovery and a `ship` checkpoint
    guarding execution. `phase_entered` defaults to a dict keyed by phase.
    """
    pdir = tmp_path / "datasets" / "programs"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(program_lib, "_program_dir", lambda root=None: str(pdir))
    monkeypatch.setattr(
        program_lib, "_counter_path", lambda root=None: str(pdir / "_counter"))

    if phase_entered is None:
        phase_entered = {"discovery": "2026-05-01"}
    if checkpoints is None:
        checkpoints = [
            {"id": "discovery-exit", "label": "Discovery exit",
             "due": "2026-05-19", "instrument": "human attestation",
             "status": "pending"},
            {"id": "ship", "label": "Ship", "due": "2026-09-15",
             "instrument": "the PM tracker", "status": "pending"},
        ]
    entered = (phase_entered.get(phase) if isinstance(phase_entered, dict)
               and scalar_entered else phase_entered)
    pid, _ = program_lib.create_program(
        type="roadmap-initiative", title="Seed", owner_role="product",
        intent="Seed intent.", root=str(tmp_path),
        frontmatter_extra={
            "phase": phase,
            "phase_entered": entered,
            "checkpoints": checkpoints,
        })
    assert pid == "PROG-0001"
    return pid


# ─── advance-phase ───────────────────────────────────────────────────────────

def test_advance_phase_sets_phase_and_stamps_entered_dict(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch)
    res = program_lib.apply_mutation(
        "PROG-0001",
        {"op": "advance-phase", "to": "planning",
         "checkpoint": "discovery-exit", "from": "discovery"},
        root=str(tmp_path))
    assert res["applied"] == "advance-phase"
    assert res["program_id"] == "PROG-0001"
    prog = program_lib.read_program("PROG-0001", root=str(tmp_path))
    fm = prog["frontmatter"]
    assert fm["phase"] == "planning"
    # dict form preserved + the new phase stamped to today
    assert isinstance(fm["phase_entered"], dict)
    assert fm["phase_entered"]["planning"]  # stamped
    assert fm["phase_entered"]["discovery"] == "2026-05-01"  # prior preserved


def test_advance_phase_appends_completion_observation(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch)
    program_lib.apply_mutation(
        "PROG-0001",
        {"op": "advance-phase", "to": "planning", "checkpoint": "discovery-exit"},
        root=str(tmp_path))
    body = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    assert "[completion]" in body
    assert "sentinel:reconciler" in body
    # source cites the checkpoint when the mutation carries one
    assert "source: checkpoint:discovery-exit" in body


def test_advance_phase_source_is_proposal_when_no_checkpoint(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch)
    program_lib.apply_mutation(
        "PROG-0001", {"op": "advance-phase", "to": "planning"},
        root=str(tmp_path))
    body = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    assert "source: proposal" in body


def test_advance_phase_preserves_scalar_entered_form(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, phase_entered={"discovery": "2026-05-01"},
                  scalar_entered=True)
    prog = program_lib.read_program("PROG-0001", root=str(tmp_path))
    assert not isinstance(prog["frontmatter"]["phase_entered"], dict)  # scalar
    program_lib.apply_mutation(
        "PROG-0001", {"op": "advance-phase", "to": "planning"},
        root=str(tmp_path))
    fm = program_lib.read_program("PROG-0001", root=str(tmp_path))["frontmatter"]
    # scalar form preserved (a bare date string, not promoted to a dict)
    assert not isinstance(fm["phase_entered"], dict)
    assert fm["phase"] == "planning"


def test_advance_phase_terminal_is_refused(tmp_path, monkeypatch):
    # verified is terminal; advancing past it is a no-op + status.
    _seed_program(tmp_path, monkeypatch, phase="verified",
                  phase_entered={"verified": "2026-06-01"})
    res = program_lib.apply_mutation(
        "PROG-0001", {"op": "advance-phase", "to": "nowhere"},
        root=str(tmp_path))
    assert res.get("applied") in (None, "none")
    assert res.get("status") == "refused"
    fm = program_lib.read_program("PROG-0001", root=str(tmp_path))["frontmatter"]
    assert fm["phase"] == "verified"  # unchanged


def test_advance_phase_marks_carried_checkpoint_met(tmp_path, monkeypatch):
    # Accepting an advance attests the exit checkpoint -> it flips to met, so the
    # program never sits in `planning` with `discovery-exit` still pending (mirrors
    # the fact door). Surfaced by the live e2e.
    _seed_program(tmp_path, monkeypatch, phase="discovery")
    program_lib.apply_mutation(
        "PROG-0001",
        {"op": "advance-phase", "to": "planning", "checkpoint": "discovery-exit"},
        root=str(tmp_path))
    fm = program_lib.read_program("PROG-0001", root=str(tmp_path))["frontmatter"]
    assert fm["phase"] == "planning"
    cp = next(c for c in fm["checkpoints"] if c["id"] == "discovery-exit")
    assert cp["status"] == "met"


def test_advance_phase_already_at_target_is_noop(tmp_path, monkeypatch):
    # Idempotent: a retried accept whose program is already at `to` must NOT
    # advance a second time (the accept-after-partial-failure double-advance fence).
    _seed_program(tmp_path, monkeypatch, phase="planning",
                  phase_entered={"planning": "2026-05-19"})
    res = program_lib.apply_mutation(
        "PROG-0001", {"op": "advance-phase", "to": "planning"}, root=str(tmp_path))
    assert res.get("status") == "noop"
    fm = program_lib.read_program("PROG-0001", root=str(tmp_path))["frontmatter"]
    assert fm["phase"] == "planning"  # not advanced to execution


def test_advance_phase_stale_target_is_refused(tmp_path, monkeypatch):
    # `to` must be the immediate successor of the current phase. From discovery the
    # next phase is planning; a proposal targeting execution is stale -> refused,
    # never advanced (no skip, no arbitrary jump).
    _seed_program(tmp_path, monkeypatch, phase="discovery")
    res = program_lib.apply_mutation(
        "PROG-0001", {"op": "advance-phase", "to": "execution"}, root=str(tmp_path))
    assert res.get("status") == "refused"
    fm = program_lib.read_program("PROG-0001", root=str(tmp_path))["frontmatter"]
    assert fm["phase"] == "discovery"  # unchanged


# ─── adjust-checkpoint ─────────────────────────────────────────────────────────

def test_adjust_checkpoint_empty_is_refused(tmp_path, monkeypatch):
    # Neither a due nor status:met -> refuse (no false success, no needless rewrite).
    _seed_program(tmp_path, monkeypatch)
    res = program_lib.apply_mutation(
        "PROG-0001", {"op": "adjust-checkpoint", "id": "ship"}, root=str(tmp_path))
    assert res.get("status") == "refused"


def test_adjust_checkpoint_changes_due(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch)
    res = program_lib.apply_mutation(
        "PROG-0001",
        {"op": "adjust-checkpoint", "id": "ship", "due": "2026-10-01"},
        root=str(tmp_path))
    assert res["applied"] == "adjust-checkpoint"
    fm = program_lib.read_program("PROG-0001", root=str(tmp_path))["frontmatter"]
    ship = next(c for c in fm["checkpoints"] if c["id"] == "ship")
    assert str(ship["due"]) == "2026-10-01"


def test_adjust_checkpoint_sets_status_met(tmp_path, monkeypatch):
    # Make ship NOT the current phase's exit checkpoint so it does not cascade.
    _seed_program(tmp_path, monkeypatch, phase="discovery")
    program_lib.apply_mutation(
        "PROG-0001",
        {"op": "adjust-checkpoint", "id": "ship", "status": "met"},
        root=str(tmp_path))
    fm = program_lib.read_program("PROG-0001", root=str(tmp_path))["frontmatter"]
    ship = next(c for c in fm["checkpoints"] if c["id"] == "ship")
    assert ship["status"] == "met"
    assert fm["phase"] == "discovery"  # no cascade (not the exit checkpoint)


def test_adjust_checkpoint_met_cascades_to_advance(tmp_path, monkeypatch):
    # discovery's exit_checkpoint is discovery-exit; marking it met cascades to
    # advance the phase via the SAME advance helper.
    _seed_program(tmp_path, monkeypatch, phase="discovery")
    res = program_lib.apply_mutation(
        "PROG-0001",
        {"op": "adjust-checkpoint", "id": "discovery-exit", "status": "met"},
        root=str(tmp_path))
    fm = program_lib.read_program("PROG-0001", root=str(tmp_path))["frontmatter"]
    cp = next(c for c in fm["checkpoints"] if c["id"] == "discovery-exit")
    assert cp["status"] == "met"
    assert fm["phase"] == "planning"  # cascaded advance
    assert res.get("advanced", {}).get("to") == "planning"


def test_adjust_checkpoint_unknown_id_refused(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch)
    res = program_lib.apply_mutation(
        "PROG-0001",
        {"op": "adjust-checkpoint", "id": "ghost", "due": "2026-10-01"},
        root=str(tmp_path))
    assert res.get("status") == "refused"


# ─── closed set enforcement + append-only ─────────────────────────────────────

def test_out_of_set_op_raises_and_leaves_program_unchanged(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch)
    before = program_lib.read_program("PROG-0001", root=str(tmp_path))
    with pytest.raises(ValueError):
        program_lib.apply_mutation(
            "PROG-0001", {"op": "delete-program"}, root=str(tmp_path))
    after = program_lib.read_program("PROG-0001", root=str(tmp_path))
    assert after["frontmatter"] == before["frontmatter"]
    assert after["body"] == before["body"]


def test_missing_op_raises(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        program_lib.apply_mutation("PROG-0001", {"to": "planning"},
                                   root=str(tmp_path))
