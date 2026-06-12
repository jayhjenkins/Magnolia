"""Tests for scripts/adapt_harness.py.

The Adapt build harness carries the full /magnolia-build steering into a
headless `claude -p --resume` session via --append-system-prompt, minus the
environment / merge / PR ornamentation that does not apply in that context,
plus a hard scope gate. These tests pin:
  (a) the steering anchors are present (so we did not lose the discipline),
  (b) the scope-gate phrase is present,
  (c) the dropped ornamentation strings are absent,
  (d) the text is byte-for-byte stable across calls and pure ASCII
      (so it is prompt-cache friendly when injected every turn).
"""

import re

from scripts.adapt_harness import build_harness_prompt


def test_contains_steering_anchors():
    text = build_harness_prompt()
    for anchor in (
        "meta-scope-extension",
        "subagent-driven-development",
        "two-stage",
        "brainstorm",
    ):
        assert anchor in text, f"missing steering anchor: {anchor!r}"


def test_contains_four_iron_law_keywords():
    text = build_harness_prompt()
    # The four iron laws, by stable keyword substring drawn from the SKILL:
    #  1. Brainstorm before building
    #  2. Gates green before every code commit
    #  3. Bind to the seam before building
    #  4. The engine stays de-personalized (capture to profile)
    for keyword in (
        "Brainstorm before building",
        "Gates green",
        "Bind to the seam",
        "de-personalized",
    ):
        assert keyword in text, f"missing iron-law keyword: {keyword!r}"


def test_contains_scope_gate_phrase():
    text = build_harness_prompt()
    assert "run Claude Code natively" in text


def test_omits_dropped_ornamentation():
    # Case-insensitive: the SKILL phrases the merge question with a capital "Merge",
    # so a lowercase-only guard would miss a reintroduction of the exact SKILL text.
    text = build_harness_prompt().lower()
    for dropped in (
        "merge to main when it's green, or open a pr",
        "preflight",
        "gh auth",
    ):
        assert dropped not in text, f"ornamentation leaked: {dropped!r}"


def test_byte_for_byte_stable_across_calls():
    assert build_harness_prompt() == build_harness_prompt()


def test_pure_ascii():
    text = build_harness_prompt()
    non_ascii = re.findall(r"[^\x00-\x7F]", text)
    assert not non_ascii, f"non-ASCII chars in harness: {non_ascii}"


def test_returns_nonempty_string():
    text = build_harness_prompt()
    assert isinstance(text, str)
    assert len(text) > 500
