"""Tests for the versioned digest-artifact writer in program_lib (Cadence inc3b).

The Monday priorities digest must be a VERSIONED artifact that is NEVER
overwritten (invariant #6). These tests pin the deterministic writer the
priority-digest worker calls, so versioning cannot be gotten wrong by a
`claude -p` Write.
"""

import os
import subprocess
import sys

import program_lib

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "program_lib.py",
)


def _seed(tmp_path, monkeypatch):
    pdir = tmp_path / "datasets" / "programs"
    pdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(program_lib, "_program_dir", lambda root=None: str(pdir))
    monkeypatch.setattr(program_lib, "_counter_path", lambda root=None: str(pdir / "_counter"))
    pid, _ = program_lib.create_program(type="weekly-priorities", title="WP",
        owner_role="product", intent="x", root=str(tmp_path))
    return pid


def test_write_artifact_versions_never_overwrites(tmp_path, monkeypatch):
    pid = _seed(tmp_path, monkeypatch)
    p1 = program_lib.write_artifact(pid, "2026-W25-priorities", "v1 body", root=str(tmp_path))
    p2 = program_lib.write_artifact(pid, "2026-W25-priorities", "v2 body", root=str(tmp_path))
    assert p1.endswith("2026-W25-priorities-v1.md")
    assert p2.endswith("2026-W25-priorities-v2.md")
    assert open(p1).read() == "v1 body"   # v1 untouched (invariant #6)
    assert open(p2).read() == "v2 body"


def test_write_artifact_path_is_under_program_artifacts(tmp_path, monkeypatch):
    pid = _seed(tmp_path, monkeypatch)
    p = program_lib.write_artifact(pid, "slug", "body", root=str(tmp_path))
    assert f"/artifacts/{pid}/" in p.replace("\\", "/")


def test_iter_recent_artifacts_returns_newest_first_capped(tmp_path, monkeypatch):
    pid = _seed(tmp_path, monkeypatch)
    for wk in ("W22", "W23", "W24", "W25"):
        program_lib.write_artifact(pid, f"2026-{wk}-priorities", f"{wk} body", root=str(tmp_path))
    recent = program_lib.iter_recent_artifacts(pid, n=3, root=str(tmp_path))
    assert len(recent) == 3
    assert "W25" in recent[0]["body"]
    assert "W22" not in [r["body"][:3] for r in recent]


def test_iter_recent_artifacts_missing_dir_returns_empty(tmp_path, monkeypatch):
    """Tolerant of a program that has never written an artifact (no dir)."""
    pid = _seed(tmp_path, monkeypatch)
    # No write_artifact call -> the artifacts dir for this program does not exist.
    assert program_lib.iter_recent_artifacts(pid, root=str(tmp_path)) == []


def test_iter_recent_artifacts_newest_version_of_newest_period_first(tmp_path, monkeypatch):
    """Sort is by (slug, version): newest version of the newest period leads."""
    pid = _seed(tmp_path, monkeypatch)
    program_lib.write_artifact(pid, "2026-W25-priorities", "w25 v1", root=str(tmp_path))
    program_lib.write_artifact(pid, "2026-W25-priorities", "w25 v2", root=str(tmp_path))
    recent = program_lib.iter_recent_artifacts(pid, n=5, root=str(tmp_path))
    assert recent[0]["slug"] == "2026-W25-priorities"
    assert recent[0]["version"] == 2
    assert recent[0]["body"] == "w25 v2"
    assert recent[1]["version"] == 1


def test_cli_write_artifact_writes_file_and_prints_path(tmp_path):
    """The CLI branch reads multi-line content from a file and prints the path."""
    # A standalone repo-rooted run: seed a real program under tmp_path's datasets.
    content_file = tmp_path / "digest.md"
    content_file.write_text("line one\nline two\n", encoding="utf-8")
    out = subprocess.check_output(
        [sys.executable, _SCRIPT, "write-artifact", "PROG-9999",
         "2026-W26-priorities", str(content_file), "--root", str(tmp_path)],
        text=True,
    ).strip()
    assert out.endswith("2026-W26-priorities-v1.md")
    assert os.path.isfile(out)
    assert open(out).read() == "line one\nline two\n"
