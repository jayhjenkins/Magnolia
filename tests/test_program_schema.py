import json

import program_schema as ps
import program_lib


def test_seed_registry_is_valid():
    assert ps.validate() == []   # the real seed registry passes


# ─── Task 6: weekly-priorities digest wiring ─────────────────────────────────

def _weekly_priorities_type():
    with open(ps.REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    return next(t for t in reg["types"] if t["id"] == "weekly-priorities")


def test_weekly_priorities_has_produce_artifact_and_draft_message_emitters():
    """weekly-priorities keeps escalate and gains the two cycle-fresh emitters
    that drive the Monday digest (worker dispatch + rate-capped nudge)."""
    wp = _weekly_priorities_type()
    actions = {(em.get("on"), em.get("action")) for em in wp["emitters"]}
    # escalate stays
    assert ("drift:broken", "escalate") in actions
    # produce-artifact dispatches the priority-digest worker
    produce = next(em for em in wp["emitters"]
                   if em.get("action") == "produce-artifact")
    assert produce["on"] == "cycle-fresh"
    assert produce["worker"] == "priority-digest"
    # draft-message carries the weekly-digest template + a nudge cap
    draft = next(em for em in wp["emitters"]
                 if em.get("action") == "draft-message")
    assert draft["on"] == "cycle-fresh"
    assert draft["template"] == "weekly-digest"
    assert draft["max_nudges_per_person_per_week"] == 1


def test_prog_0005_items_survive_read_program():
    """Option 1 seam check: the seeded role-referenced items survive the read
    and are available to the worker (which is how items are consumed). No
    person/company names leak into the seed."""
    prog = program_lib.read_program("PROG-0005")
    items = prog["frontmatter"]["items"]
    assert items, "PROG-0005 must seed a non-empty items list"
    for it in items:
        assert isinstance(it, dict)
        assert it.get("owner_role")          # role token present
        assert "owner" not in it             # no person token
        assert "company" not in it           # no company token


# ─── sentinel defs are validated by the gate (Task 2) ─────────────────────────


def _write_sentinel(d, frontmatter, body="Read sources and attribute signals."):
    import io
    from ruamel.yaml import YAML
    sd = d / "scripts" / "sentinels"
    sd.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    YAML().dump(frontmatter, stream)
    (sd / (frontmatter["name"] + ".md")).write_text(
        "---\n" + stream.getvalue() + "---\n" + body, encoding="utf-8")
    return sd


def _good_sentinel_fm(name="probe"):
    return {
        "name": name,
        "kind": "sentinel",
        "sources": [{"kind": "transcripts", "mode": "read"}],
        "observation_kinds": ["status-signal", "completion"],
        "scope": "active-programs",
    }


def test_shipped_sentinels_pass_the_gate():
    # The real gate over the real shipped sentinel dir is clean.
    assert ps.validate() == []


def test_gate_rejects_sentinel_write_source(tmp_path):
    fm = _good_sentinel_fm()
    fm["sources"] = [{"kind": "transcripts", "mode": "write"}]
    sd = _write_sentinel(tmp_path, fm)
    errs = ps.validate_sentinels(str(sd))
    assert any("mode" in e.lower() for e in errs)


def test_gate_rejects_sentinel_bad_kind(tmp_path):
    fm = _good_sentinel_fm()
    fm["kind"] = "worker"
    sd = _write_sentinel(tmp_path, fm)
    errs = ps.validate_sentinels(str(sd))
    assert any("kind" in e.lower() for e in errs)


def test_gate_accepts_good_sentinel_dir(tmp_path):
    sd = _write_sentinel(tmp_path, _good_sentinel_fm())
    assert ps.validate_sentinels(str(sd)) == []


def test_gate_validates_sentinels_in_a_dir_of_any_shape(tmp_path):
    # The gate must not reconstruct a root from the dir shape: a sentinels dir
    # whose path is NOT <root>/scripts/sentinels still validates correctly.
    import io
    from ruamel.yaml import YAML
    flat = tmp_path / "elsewhere"
    flat.mkdir()
    fm = _good_sentinel_fm()
    fm["sources"] = [{"kind": "transcripts", "mode": "write"}]
    stream = io.StringIO()
    YAML().dump(fm, stream)
    (flat / "probe.md").write_text(
        "---\n" + stream.getvalue() + "---\nbody", encoding="utf-8")
    errs = ps.validate_sentinels(str(flat))
    assert any("mode" in e.lower() for e in errs)  # loaded + validated, no path error


def test_rejects_unknown_state_model():
    reg = {"families": [{"id": "x", "label": "X", "order": 1}],
           "types": [{"id": "t", "label": "T", "family": "x", "state_model": "workflow",
                      "sources": [], "presentation": {"chip_tokens": {}}}]}
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("state_model" in e for e in errs)


def test_rejects_phases_on_non_pipeline():
    reg = {"families": [{"id": "x", "label": "X", "order": 1}],
           "types": [{"id": "t", "label": "T", "family": "x", "state_model": "cycle",
                      "phases": [{"id": "p", "label": "P"}],
                      "sources": [], "presentation": {"chip_tokens": {}}}]}
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("phases" in e for e in errs)


def test_rejects_unknown_family():
    reg = {"families": [{"id": "x", "label": "X", "order": 1}],
           "types": [{"id": "t", "label": "T", "family": "nope", "state_model": "cycle",
                      "sources": [], "presentation": {"chip_tokens": {}}}]}
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("family" in e for e in errs)


def test_rejects_non_token_presentation():
    reg = {"families": [{"id": "x", "label": "X", "order": 1}],
           "types": [{"id": "t", "label": "T", "family": "x", "state_model": "cycle", "sources": [],
                      "presentation": {"chip_tokens": {"a": "#ff0000"}}}]}
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("token" in e for e in errs)


def test_rejects_source_without_mode():
    reg = {"families": [{"id": "x", "label": "X", "order": 1}],
           "types": [{"id": "t", "label": "T", "family": "x", "state_model": "cycle",
                      "sources": [{"kind": "transcripts"}], "presentation": {"chip_tokens": {}}}]}
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("mode" in e for e in errs)


# ─── emitters (Task 3) ────────────────────────────────────────────────────────


def _type_with_emitters(emitters):
    return {"families": [{"id": "x", "label": "X", "order": 1}],
            "types": [{"id": "t", "label": "T", "family": "x", "state_model": "cycle",
                       "sources": [], "presentation": {"chip_tokens": {}},
                       "emitters": emitters}]}


def test_accepts_valid_emitter():
    reg = _type_with_emitters([{"on": "drift:broken", "action": "escalate"}])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert errs == []


def test_rejects_unknown_emitter_action():
    reg = _type_with_emitters([{"on": "drift:broken", "action": "frobnicate"}])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("frobnicate" in e and "t" in e for e in errs)


def test_rejects_emitter_missing_on():
    reg = _type_with_emitters([{"action": "escalate"}])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("on" in e for e in errs)


def test_rejects_emitter_empty_on():
    reg = _type_with_emitters([{"on": "  ", "action": "escalate"}])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("on" in e for e in errs)


def test_rejects_emitters_not_a_list():
    reg = _type_with_emitters({"on": "drift:broken", "action": "escalate"})
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("emitters" in e for e in errs)


def test_rejects_emitter_not_a_dict():
    reg = _type_with_emitters(["escalate"])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("emitter" in e for e in errs)


# ─── nudge cap + type-level default items (Task 2) ───────────────────────────


def test_rejects_non_int_nudge_cap():
    reg = _type_with_emitters([
        {"on": "cycle-fresh", "action": "draft-message",
         "max_nudges_per_person_per_week": "lots"}])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("max_nudges_per_person_per_week" in e for e in errs)


def test_accepts_int_nudge_cap():
    reg = _type_with_emitters([
        {"on": "cycle-fresh", "action": "draft-message",
         "max_nudges_per_person_per_week": 1}])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert errs == []


def test_rejects_negative_nudge_cap():
    reg = _type_with_emitters([
        {"on": "cycle-fresh", "action": "draft-message",
         "max_nudges_per_person_per_week": -1}])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("max_nudges_per_person_per_week" in e for e in errs)


def test_rejects_bool_nudge_cap():
    # bool is an int subclass in Python; it must be rejected explicitly.
    reg = _type_with_emitters([
        {"on": "cycle-fresh", "action": "draft-message",
         "max_nudges_per_person_per_week": True}])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("max_nudges_per_person_per_week" in e for e in errs)


def _type_with_items(items):
    return {"families": [{"id": "x", "label": "X", "order": 1}],
            "types": [{"id": "t", "label": "T", "family": "x", "state_model": "cycle",
                       "sources": [], "presentation": {"chip_tokens": {}},
                       "items": items}]}


def test_rejects_non_list_default_items():
    reg = _type_with_items("nope")
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("items" in e for e in errs)


def test_rejects_non_dict_default_item():
    reg = _type_with_items([{"ok": True}, "bad"])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("items" in e for e in errs)


def test_accepts_list_of_dict_default_items():
    reg = _type_with_items([{"id": "a"}, {"id": "b"}])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert errs == []


# ─── exit_checkpoint on pipeline phases (Task 5) ─────────────────────────────


def _pipeline_with_phases(phases):
    return {"families": [{"id": "x", "label": "X", "order": 1}],
            "types": [{"id": "t", "label": "T", "family": "x", "state_model": "pipeline",
                       "phases": phases,
                       "sources": [], "presentation": {"chip_tokens": {}}}]}


def test_accepts_exit_checkpoint_string_on_pipeline_phase():
    reg = _pipeline_with_phases([
        {"id": "discovery", "label": "D", "exit_checkpoint": "discovery-exit"},
        {"id": "shipped", "label": "S", "terminal": True},
    ])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert errs == []


def test_rejects_non_string_exit_checkpoint():
    reg = _pipeline_with_phases([
        {"id": "discovery", "label": "D", "exit_checkpoint": 42},
        {"id": "shipped", "label": "S", "terminal": True},
    ])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("exit_checkpoint" in e for e in errs)


# ─── intake block (Task 1: birth path) ───────────────────────────────────────


def _type_with_intake(intake):
    return {"families": [{"id": "x", "label": "X", "order": 1}],
            "types": [{"id": "t", "label": "T", "family": "x", "state_model": "cycle",
                       "sources": [], "presentation": {"chip_tokens": {}},
                       "intake": intake}]}


def test_accepts_candidate_intake_with_valid_birth_threshold():
    reg = _type_with_intake({
        "route": "candidate",
        "signals": ["recurring theme across discovery calls"],
        "birth_threshold": {"min_independent_sources": 2,
                            "or_explicit_declaration": True},
        "bootstrap_emissions": [
            {"action": "draft-ticket", "template": "create-tracker"},
            {"action": "propose-update", "template": "add-roadmap-entry"},
        ],
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert errs == []


def test_accepts_explicit_declaration_only_birth_threshold():
    reg = _type_with_intake({
        "route": "candidate",
        "birth_threshold": {"explicit_declaration_only": True},
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert errs == []


def test_accepts_capture_route_without_birth_threshold():
    reg = _type_with_intake({"route": "capture"})
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert errs == []


def test_rejects_unknown_intake_route():
    reg = _type_with_intake({"route": "bogus"})
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("route" in e and "bogus" in e for e in errs)


def test_rejects_candidate_route_without_birth_threshold():
    reg = _type_with_intake({"route": "candidate"})
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("birth_threshold" in e for e in errs)


def test_rejects_bool_min_independent_sources():
    # bool is an int subclass in Python; it must be rejected explicitly.
    reg = _type_with_intake({
        "route": "candidate",
        "birth_threshold": {"min_independent_sources": True},
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("min_independent_sources" in e for e in errs)


def test_rejects_negative_min_independent_sources():
    reg = _type_with_intake({
        "route": "candidate",
        "birth_threshold": {"min_independent_sources": -1},
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("min_independent_sources" in e for e in errs)


def test_rejects_empty_birth_threshold():
    # at least one of the three recognized keys must be present.
    reg = _type_with_intake({
        "route": "candidate",
        "birth_threshold": {},
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("birth_threshold" in e for e in errs)


def test_rejects_non_bool_or_explicit_declaration():
    reg = _type_with_intake({
        "route": "candidate",
        "birth_threshold": {"or_explicit_declaration": "yes"},
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("or_explicit_declaration" in e for e in errs)


def test_rejects_bootstrap_emission_action_outside_closed_set():
    reg = _type_with_intake({
        "route": "candidate",
        "birth_threshold": {"explicit_declaration_only": True},
        "bootstrap_emissions": [{"action": "frobnicate"}],
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("frobnicate" in e for e in errs)


def test_rejects_bootstrap_emissions_not_a_list():
    reg = _type_with_intake({
        "route": "candidate",
        "birth_threshold": {"explicit_declaration_only": True},
        "bootstrap_emissions": {"action": "draft-ticket"},
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("bootstrap_emissions" in e for e in errs)


def test_rejects_non_dict_bootstrap_emission():
    reg = _type_with_intake({
        "route": "candidate",
        "birth_threshold": {"explicit_declaration_only": True},
        "bootstrap_emissions": ["draft-ticket"],
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("bootstrap_emissions" in e for e in errs)


def test_rejects_non_string_signal():
    reg = _type_with_intake({
        "route": "candidate",
        "birth_threshold": {"explicit_declaration_only": True},
        "signals": ["ok", "  "],
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("signal" in e for e in errs)


def test_rejects_signals_not_a_list():
    reg = _type_with_intake({
        "route": "candidate",
        "birth_threshold": {"explicit_declaration_only": True},
        "signals": "recurring theme",
    })
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("signals" in e for e in errs)


# ─── Task 2: program-intake type + intake blocks + seeded nursery ────────────


def _type(reg, tid):
    return next(t for t in reg["types"] if t["id"] == tid)


def test_shipped_registry_validates_after_intake_edits():
    """The real shipped registry.json still passes the gate after Task 2 adds
    the program-intake type, the system family, and the per-type intake blocks."""
    assert ps.validate() == []


def test_program_intake_type_present_and_shaped():
    with open(ps.REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    # The system family shelves last (highest order).
    fams = {f["id"]: f for f in reg["families"]}
    assert "system" in fams
    assert fams["system"]["order"] == max(f["order"] for f in reg["families"])
    pi = _type(reg, "program-intake")
    assert pi["state_model"] == "register"
    assert pi["family"] == "system"
    assert pi["cadence"] == "weekly"
    # The nursery itself is not a discovered type: no intake block.
    assert "intake" not in pi
    actions = {(em["on"], em["action"]) for em in pi["emitters"]}
    assert ("candidate-ripe", "propose-update") in actions
    assert ("drift:broken", "escalate") in actions


def test_roadmap_initiative_has_full_candidate_intake():
    with open(ps.REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    intake = _type(reg, "roadmap-initiative")["intake"]
    assert intake["route"] == "candidate"
    assert intake["birth_threshold"] == {"min_independent_sources": 2,
                                         "or_explicit_declaration": True}
    assert len(intake["signals"]) == 3
    boot = {(b["action"], b["template"]) for b in intake["bootstrap_emissions"]}
    assert ("draft-ticket", "create-tracker-initiative") in boot
    assert ("propose-update", "add-roadmap-entry") in boot


def test_cycle_types_route_capture():
    with open(ps.REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    for tid in ("weekly-priorities", "eng-sync-prep", "eos-cycle"):
        intake = _type(reg, tid)["intake"]
        assert intake["route"] == "capture"
        assert "birth_threshold" not in intake


def test_eos_rock_is_explicit_declaration_only():
    with open(ps.REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    intake = _type(reg, "eos-rock")["intake"]
    assert intake["route"] == "candidate"
    assert intake["birth_threshold"] == {"explicit_declaration_only": True}
    assert intake["signals"]  # non-empty


def test_seeded_program_intake_program_parses_active():
    """The seeded nursery program reads back as type program-intake, active."""
    prog = program_lib.read_program("PROG-0014")
    fm = prog["frontmatter"]
    assert fm["type"] == "program-intake"
    assert fm["status"] == "active"
    # role token, never a person/team name (invariant #1).
    assert fm["owner_role"] == "product"
    assert fm["items"] == []


# ─── Task 3: archive fields, triggers, portfolio-health type ─────────────────


def test_archive_after_silent_cycles_accepts_nonneg_int():
    """archive_after_silent_cycles field accepts non-negative ints."""
    reg = _type_with_emitters([])
    reg["types"][0]["archive_after_silent_cycles"] = 10
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert errs == []


def test_archive_after_silent_cycles_rejects_bool():
    """archive_after_silent_cycles rejects bool (even though bool is int subclass)."""
    reg = _type_with_emitters([])
    reg["types"][0]["archive_after_silent_cycles"] = True
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("archive_after_silent_cycles" in e for e in errs)


def test_archive_after_silent_cycles_rejects_negative():
    """archive_after_silent_cycles rejects negative values."""
    reg = _type_with_emitters([])
    reg["types"][0]["archive_after_silent_cycles"] = -1
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert any("archive_after_silent_cycles" in e for e in errs)


def test_completion_verified_and_silent_too_long_emitter_triggers_valid():
    """New emitter triggers: completion-verified and silent-too-long are valid."""
    reg = _type_with_emitters([
        {"on": "completion-verified", "action": "propose-update"},
        {"on": "silent-too-long", "action": "propose-update"}
    ])
    errs = ps.validate_doc(reg, tokens={"--accent"})
    assert errs == []


def test_portfolio_health_type_validates():
    """portfolio-health type is present in registry and validates."""
    with open(ps.REGISTRY, encoding="utf-8") as f:
        reg = json.load(f)
    # Find portfolio-health type
    ph = next((t for t in reg["types"] if t["id"] == "portfolio-health"), None)
    assert ph is not None, "portfolio-health type not in registry"
    assert ph["state_model"] == "register"
    assert ph["family"] == "system"
    # Validate the entire registry (which includes this type)
    errs = ps.validate_doc(reg, tokens=ps._theme_tokens())
    assert errs == [], f"portfolio-health type validation failed: {errs}"
