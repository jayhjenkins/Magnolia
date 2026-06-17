"""Tests for program_lib.append_observation — the append-only observation writer.

The observation writer is the foundation of the Cadence interpretation engine:
sentinels (later tasks) produce structured observations; this deterministic,
validated function appends them under a program's `## Observations` section.
The LLM never writes files — this function does.

Isolation: every test confines program_lib to a tmp dir (via the `root` arg,
belt-and-suspenders monkeypatching `_program_dir`/`_counter_path`), so the real
`datasets/` is never touched.
"""

import pytest

import program_lib


# The closed observation-kind enum (mirrors program_lib.OBSERVATION_KINDS).
OBS_KINDS = {"status-signal", "date-change", "completion", "commitment",
             "risk", "metric", "capture", "blocker"}


def _seed_program(tmp_path, monkeypatch, program_id="PROG-0001"):
    """Create a program in an isolated tmp dir and return its id.

    Pins program_lib's program dir + counter path to tmp_path so nothing
    touches the real datasets/, then creates a program via create_program.
    The first created program is always PROG-0001 (the counter seeds at 1).
    """
    pdir = tmp_path / "datasets" / "programs"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(program_lib, "_program_dir", lambda root=None: str(pdir))
    monkeypatch.setattr(
        program_lib, "_counter_path", lambda root=None: str(pdir / "_counter"))
    pid, _ = program_lib.create_program(
        type="roadmap-initiative", title="Seed", owner_role="product",
        intent="Seed intent.", root=str(tmp_path))
    assert pid == program_id
    return pid


def test_append_observation_writes_under_section(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    ok = program_lib.append_observation(
        "PROG-0001", kind="status-signal", sentinel="movement-watch",
        source="datasets/meetings/2026-06-11_x.md (#Action Items)",
        claim="Discovery spike reported complete.", root=str(tmp_path))
    assert ok is True
    prog = program_lib.read_program("PROG-0001", root=str(tmp_path))
    body = prog["body"]
    assert "sentinel:movement-watch [status-signal]" in body
    assert "Discovery spike reported complete." in body
    assert "source: datasets/meetings/2026-06-11_x.md (#Action Items)" in body
    # The entry sits under ## Observations and before ## Cycles.
    obs_idx = body.index("## Observations")
    cyc_idx = body.index("## Cycles")
    entry_idx = body.index("sentinel:movement-watch")
    assert obs_idx < entry_idx < cyc_idx


def test_append_observation_header_uses_ascii_hyphen(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    program_lib.append_observation(
        "PROG-0001", kind="risk", sentinel="movement-watch",
        source="s", claim="c", root=str(tmp_path))
    body = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    # No em-dash or en-dash anywhere in the emitted body (invariant #8).
    assert "—" not in body
    assert "–" not in body
    assert " - sentinel:movement-watch [risk]" in body


def test_append_observation_appends_only(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    program_lib.append_observation(
        "PROG-0001", kind="completion", sentinel="movement-watch",
        source="datasets/meetings/a.md", claim="First done.", root=str(tmp_path))
    program_lib.append_observation(
        "PROG-0001", kind="risk", sentinel="movement-watch",
        source="datasets/meetings/b.md", claim="Second risk.", root=str(tmp_path))
    body = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    # The first observation survives the second append (append-only, invariant #6).
    assert "First done." in body
    assert "Second risk." in body
    assert body.index("First done.") < body.index("Second risk.")


def test_append_observation_includes_confidence_when_given(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    program_lib.append_observation(
        "PROG-0001", kind="metric", sentinel="tracker-truth",
        source="adapter:asana", claim="NPS up 3.", confidence=0.9,
        root=str(tmp_path))
    body = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    assert "confidence: 0.9" in body


def test_append_observation_formats_confidence_to_two_decimals(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    program_lib.append_observation(
        "PROG-0001", kind="metric", sentinel="tracker-truth",
        source="adapter:asana", claim="float precision.",
        confidence=0.30000000000000004, root=str(tmp_path))
    body = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    assert "confidence: 0.30" in body
    assert "0.30000000000000004" not in body


def test_append_observation_omits_confidence_when_absent(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    program_lib.append_observation(
        "PROG-0001", kind="capture", sentinel="movement-watch",
        source="s", claim="c", root=str(tmp_path))
    body = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    assert "confidence:" not in body


def test_append_observation_dedupes_identical(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    kw = dict(kind="completion", sentinel="movement-watch",
              source="datasets/meetings/x.md", claim="Done.", root=str(tmp_path))
    assert program_lib.append_observation("PROG-0001", **kw) is True
    before = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    assert program_lib.append_observation("PROG-0001", **kw) is False  # dedupe
    after = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    assert before == after  # file unchanged on dedupe
    assert after.count("Done.") == 1


def test_append_observation_dedupe_ignores_sentinel_and_date(tmp_path, monkeypatch):
    """Dedupe hashes (kind, source, claim) only — a different sentinel/date
    citing the same claim is still a dupe."""
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    assert program_lib.append_observation(
        "PROG-0001", kind="risk", sentinel="movement-watch",
        source="s", claim="Same claim.", root=str(tmp_path)) is True
    assert program_lib.append_observation(
        "PROG-0001", kind="risk", sentinel="tracker-truth",
        source="s", claim="Same claim.", date="2020-01-01",
        root=str(tmp_path)) is False


def test_append_observation_rejects_bad_kind(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    with pytest.raises(ValueError):
        program_lib.append_observation(
            "PROG-0001", kind="vibes", sentinel="x", source="s", claim="c",
            root=str(tmp_path))


def test_append_observation_requires_source(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    with pytest.raises(ValueError):
        program_lib.append_observation(
            "PROG-0001", kind="risk", sentinel="x", source="", claim="c",
            root=str(tmp_path))


def test_append_observation_requires_claim(tmp_path, monkeypatch):
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    with pytest.raises(ValueError):
        program_lib.append_observation(
            "PROG-0001", kind="risk", sentinel="x", source="s", claim="   ",
            root=str(tmp_path))


def test_append_observation_preserves_following_cycles(tmp_path, monkeypatch):
    """A ## Cycles section after Observations is preserved verbatim."""
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    # Seed a real cycle entry so there is content under ## Cycles to preserve.
    prog = program_lib.read_program("PROG-0001", root=str(tmp_path))
    fm, body = prog["frontmatter"], prog["body"]
    seeded = body.replace(
        "## Cycles\n",
        "## Cycles\n\n### 2026-W24 - holding\nchecks: ok - emitted: none - next: review\n")
    program_lib._write_program_file(prog["filepath"], fm, seeded)

    program_lib.append_observation(
        "PROG-0001", kind="status-signal", sentinel="movement-watch",
        source="s", claim="A new signal.", root=str(tmp_path))
    body = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    # Cycle content intact, still after the observation.
    assert "### 2026-W24 - holding" in body
    assert "checks: ok - emitted: none - next: review" in body
    assert body.index("A new signal.") < body.index("### 2026-W24 - holding")
    # Observations header is still present and the section order holds.
    assert body.index("## Observations") < body.index("A new signal.") < body.index("## Cycles")


def test_append_observation_roundtrips_via_parser(tmp_path, monkeypatch):
    """The emitted entry is readable by the existing _parse_observations."""
    _seed_program(tmp_path, monkeypatch, "PROG-0001")
    program_lib.append_observation(
        "PROG-0001", kind="completion", sentinel="movement-watch",
        source="datasets/meetings/x.md", claim="Parseable claim.",
        root=str(tmp_path))
    body = program_lib.read_program("PROG-0001", root=str(tmp_path))["body"]
    entries = program_lib._parse_observations(body)
    assert any(e["text"] == "Parseable claim." for e in entries)
