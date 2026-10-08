"""The Doctor's python-deps check covers the root requirements.txt (the board
server's runtime deps), not just ruamel.yaml."""
import os

import doctor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_python_deps_cover_root_requirements():
    # croniter/requests missing used to crash the server with no visible error.
    assert {"ruamel.yaml", "croniter", "requests"} <= set(doctor._PYTHON_DEPS)


def test_python_deps_match_requirements_file():
    assert doctor._requirements_modules(os.path.join(ROOT, "requirements.txt")) == doctor._PYTHON_DEPS


def test_requirements_modules_parses_specifiers(tmp_path):
    req = tmp_path / "requirements.txt"
    req.write_text("# comment\nruamel.yaml>=0.18\n\n"
                   "foo-bar[extra]==1.0 ; python_version>'3'\nbaz  # trailing\n")
    assert doctor._requirements_modules(str(req)) == ["ruamel.yaml", "foo_bar", "baz"]


def test_requirements_modules_falls_back_when_missing(tmp_path):
    assert doctor._requirements_modules(str(tmp_path / "nope.txt")) == ["ruamel.yaml"]


def test_probe_python_deps_missing_has_requirements_remedy(monkeypatch):
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda n: None)
    cap = doctor.probe_python_deps(["croniter"])
    assert cap["status"] == "degraded"
    assert "requirements.txt" in cap["remedy"]
