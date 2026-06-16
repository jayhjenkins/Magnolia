import program_schema as ps


def test_seed_registry_is_valid():
    assert ps.validate() == []   # the real seed registry passes


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
