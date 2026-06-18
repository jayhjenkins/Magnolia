import program_schema as ps


def test_seed_registry_is_valid():
    assert ps.validate() == []   # the real seed registry passes


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
