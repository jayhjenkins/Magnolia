"""The engine must ship EMPTY per-user dataset dirs - not dev/demo content.

datasets/adaptations/ (Adapt-tab records) and datasets/programs/ (Cadence
program instances) are generated at runtime. Shipping seed PROG-* files or demo
adaptations makes a fresh install look pre-populated with fake data. These dirs
must track nothing but their .gitkeep.
"""
import pathlib
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _tracked(subdir):
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", subdir],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("not a git work tree")
    return [line for line in out.splitlines() if line.strip()]


@pytest.mark.parametrize("subdir", ["datasets/adaptations", "datasets/programs"])
def test_generated_dataset_dirs_ship_only_gitkeep(subdir):
    tracked = _tracked(subdir)
    assert tracked, f"{subdir} should track its .gitkeep"
    extras = [f for f in tracked if pathlib.PurePosixPath(f).name != ".gitkeep"]
    assert not extras, f"{subdir} must ship empty - remove tracked dev/demo content: {extras}"


def test_gitignore_excludes_generated_dataset_dirs():
    gi = (REPO / ".gitignore").read_text()
    assert "datasets/adaptations/*" in gi
    assert "datasets/programs/*" in gi
