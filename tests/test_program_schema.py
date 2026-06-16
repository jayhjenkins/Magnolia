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
