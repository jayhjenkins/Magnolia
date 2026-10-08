"""Fresh-Windows install blockers: install.ps1 shape (PATH refresh, Store-stub
python, python3 shim, PYTHONUTF8, exit-code checks), `claude auth login`,
requirements.txt, .gitattributes line endings, and task.sh's python pick.

install.ps1 can't run here, so its checks are structural; task.sh is exercised
for real against a fake ...\\WindowsApps stub on PATH."""
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


PS1 = _read("install.ps1")


# --- requirements.txt ---------------------------------------------------------

def test_requirements_cover_server_runtime_deps():
    names = {re.split(r"[<>=!~\[ ;]", l.strip())[0].lower()
             for l in _read("requirements.txt").splitlines()
             if l.strip() and not l.lstrip().startswith("#")}
    assert {"ruamel.yaml", "croniter", "requests"} <= names


# --- claude auth login ----------------------------------------------------------

@pytest.mark.parametrize("rel", ["install.ps1", "install.sh", "docs/INSTALL-smoke-checklist.md",
                                 "docs/INSTALL-windows.md", "docs/INSTALL-macos.md"])
def test_no_invalid_claude_login(rel):
    body = _read(rel)
    assert not re.search(r"claude login\b", body), f"{rel}: use `claude auth login`"


def test_installers_use_claude_auth_login():
    assert "claude auth login" in PS1
    assert "claude auth login" in _read("install.sh")


# --- install.ps1 ----------------------------------------------------------------

def test_ps1_installs_from_requirements_after_clone():
    assert "requirements.txt" in PS1
    assert "ruamel.yaml pytest" not in PS1
    assert PS1.index("git clone") < PS1.index("-m pip install -r")
    assert "import task_server" in PS1                  # server import check


def test_ps1_refreshes_path_from_registry_before_npm_and_pip():
    assert '[Environment]::GetEnvironmentVariable("Path", "Machine")' in PS1
    assert '[Environment]::GetEnvironmentVariable("Path", "User")' in PS1
    first_winget = PS1.index("winget install --id")
    refresh = PS1.index("Update-SessionPath", first_winget)
    assert refresh < PS1.index("npm install -g @tobilu/qmd")
    assert refresh < PS1.index("-m pip install")


def test_ps1_resolves_real_python_not_store_stub():
    assert "WindowsApps" in PS1
    assert "py -3" in PS1
    # pip / trust_seed run through the resolved interpreter, never bare `python`
    assert "& $Py -m pip install" in PS1
    assert "& $Py (Join-Path $Dest \"scripts\\trust_seed.py\")" in PS1
    assert not re.search(r"^\s*python\s+", PS1, re.M)


def test_ps1_checks_exit_codes_after_pip_and_trust_seed():
    pip_at = PS1.index("& $Py -m pip install")
    assert "$LASTEXITCODE" in PS1[pip_at:pip_at + 200]
    seed_at = PS1.index("trust_seed.py\") seed")
    assert "$LASTEXITCODE" in PS1[seed_at:seed_at + 200]
    assert "try { python" not in PS1                     # no silent swallow


def test_ps1_sets_pythonutf8_persistently_and_in_session():
    assert '[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")' in PS1
    assert '$env:PYTHONUTF8 = "1"' in PS1


def test_ps1_python3_shim_cmd_and_git_bash_and_prepends_path():
    assert 'Join-Path $env:LOCALAPPDATA "Magnolia\\shims"' in PS1
    assert 'Write-PythonShim "python3" $Py' in PS1
    assert '"$name.cmd"' in PS1                          # cmd/PowerShell shim
    assert "(Join-Path $ShimDir $name)" in PS1           # extensionless Git Bash shim
    assert "#!/bin/sh" in PS1 and 'exec `"$shPy`" `"`$@`"' in PS1
    assert "UTF8Encoding($false)" in PS1                 # no BOM before the shebang
    assert "Add-UserPath $ShimDir -Prepend" in PS1
    assert "DoNotExpandEnvironmentNames" in PS1          # keeps %VARS% in the User PATH
    assert "ExpandString" in PS1


def test_ps1_is_ps51_compatible_and_ascii():
    # Windows PowerShell 5.1 has no ?? / ?. / ternary / && || pipeline chains.
    code = "\n".join(l for l in PS1.splitlines() if not l.lstrip().startswith("#"))
    assert "??" not in code and "?." not in code
    assert not re.search(r"\s(&&|\|\|)\s", code)
    assert PS1.isascii()
    assert PS1.count("{") == PS1.count("}")
    assert PS1.count("@'") == PS1.count("'@")


def test_ps1_never_exits_the_hosting_shell():
    # Under `irm | iex`, `exit` would close the user's window; the body is one
    # script block that stops with `return`.
    assert not re.search(r"^\s*exit\b|[;{]\s*exit\s+\d", PS1, re.M)
    assert PS1.lstrip().splitlines()[0].startswith("#")
    assert re.search(r"^& \{$", PS1, re.M) and PS1.rstrip().endswith("}")


# --- .gitattributes ---------------------------------------------------------------

def test_gitattributes_keeps_shell_scripts_lf():
    if not shutil.which("git"):
        pytest.skip("git not available")
    out = subprocess.run(["git", "-C", ROOT, "check-attr", "eol", "--",
                          "install.sh", "scripts/task.sh", "bin/magnolia",
                          "bin/magnolia.cmd", "install.ps1"],
                         capture_output=True, text=True).stdout
    assert "install.sh: eol: lf" in out
    assert "scripts/task.sh: eol: lf" in out
    assert "bin/magnolia: eol: lf" in out
    assert "bin/magnolia.cmd: eol: crlf" in out
    assert "install.ps1: eol: crlf" in out


# --- task.sh python pick ----------------------------------------------------------

def _exe(path, body):
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def store_stub(tmp_path):
    wa = tmp_path / "WindowsApps"
    wa.mkdir()
    for n in ("python3", "python"):
        _exe(wa / n, "#!/bin/sh\necho 'Python was not found; run without arguments to install from the Microsoft Store' >&2\nexit 9009\n")
    return wa


def _run_task_sh(tmp_path, path_dirs, *args):
    bash = shutil.which("bash")
    dirname = shutil.which("dirname")
    if not bash or not dirname:
        pytest.skip("bash/dirname not available")
    # A PATH holding ONLY our fixtures plus `dirname` (task.sh needs it), so a
    # system python3 in /usr/bin can't mask the behavior under test.
    tools = tmp_path / "tools"
    tools.mkdir()
    os.symlink(dirname, tools / "dirname")
    env = dict(os.environ, PATH=os.pathsep.join(str(d) for d in [*path_dirs, tools]))
    return subprocess.run([bash, os.path.join(ROOT, "scripts", "task.sh"), *args],
                          capture_output=True, text=True, env=env)


def test_task_sh_skips_store_stub_for_real_python(tmp_path, store_stub):
    # Both python3 AND python stubs sit first on PATH (as on real Windows); the
    # real interpreter is a later PATH entry, which `command -v` alone would miss.
    real = tmp_path / "real"
    real.mkdir()
    _exe(real / "python", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    r = _run_task_sh(tmp_path, [store_stub, real], "--help")
    assert r.returncode == 0, r.stderr
    assert "usage" in r.stdout.lower()


def test_task_sh_falls_back_to_py_launcher(tmp_path, store_stub):
    launcher = tmp_path / "launcher"
    launcher.mkdir()
    _exe(launcher / "py", '#!/bin/sh\necho "PY-LAUNCHER $*"\n')
    r = _run_task_sh(tmp_path, [store_stub, launcher], "inbox")
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("PY-LAUNCHER -3 ")
    assert r.stdout.rstrip().endswith("task_cli.py inbox")
