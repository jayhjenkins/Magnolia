"""inc5 slice 10 - cadence/starter-sets.yaml + loader.

A starter set is onboarding-only (never consulted at runtime). The guard that
matters: every type id a bundle references must exist in the program-type
registry (no dangling starter set).
"""
import textwrap

import starter_sets


def test_eos_bundle_lists_the_eos_types():
    b = starter_sets.bundle("eos")
    assert set(b["types"]) == {"eos-l10-prep", "eos-rock"}
    assert b["label"]


def test_all_five_bundles_exist():
    sets = starter_sets.load_starter_sets()
    assert set(sets["sets"].keys()) == {"roadmap", "weekly", "eng-sync", "outcomes", "eos"}


def test_roadmap_bundle():
    b = starter_sets.bundle("roadmap")
    assert set(b["types"]) == {"roadmap-initiative"}


def test_outcomes_bundle():
    b = starter_sets.bundle("outcomes")
    assert set(b["types"]) == {"did-it-work"}


def test_real_starter_sets_validate_against_the_registry():
    # The shipped starter-sets.yaml references only real registry type ids.
    assert starter_sets.validate() == []


def test_validate_flags_a_dangling_type(tmp_path):
    p = tmp_path / "starter-sets.yaml"
    p.write_text(textwrap.dedent("""\
        sets:
          bad:
            label: "Bad"
            types:
              - eos-rock
              - no-such-type
    """))
    errs = starter_sets.validate(path=str(p))
    assert any("no-such-type" in e for e in errs)
    assert not any("eos-rock" in e for e in errs)   # the real one is fine


def test_validate_flags_empty_types(tmp_path):
    p = tmp_path / "starter-sets.yaml"
    p.write_text(textwrap.dedent("""\
        sets:
          empty:
            label: "Empty"
            types: []
    """))
    errs = starter_sets.validate(path=str(p))
    assert any("empty" in e for e in errs)


def test_missing_file_is_empty_not_an_error(tmp_path):
    assert starter_sets.load_starter_sets(path=str(tmp_path / "nope.yaml")) == {}
