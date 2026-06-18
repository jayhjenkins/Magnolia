import os
import shutil
import stat
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_unix_shim_exists_executable_and_targets_launcher():
    p = os.path.join(ROOT, "bin", "magnolia")
    assert os.path.isfile(p)
    assert os.stat(p).st_mode & stat.S_IXUSR        # executable
    body = open(p, encoding="utf-8").read()
    assert body.startswith("#!")                     # has a shebang
    assert "scripts/magnolia.py" in body             # routes to the launcher
    assert "readlink" in body                        # resolves its own symlink to find the repo


def test_windows_shim_targets_launcher():
    p = os.path.join(ROOT, "bin", "magnolia.cmd")
    assert os.path.isfile(p)
    body = open(p, encoding="utf-8").read()
    assert "magnolia.py" in body
    assert "%*" in body                              # forwards args


def test_install_sh_parses_clean():
    p = os.path.join(ROOT, "install.sh")
    assert os.path.isfile(p)
    bash = shutil.which("bash")
    if not bash:
        return  # environment without bash; shape checks below still apply
    r = subprocess.run([bash, "-n", p], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_install_sh_has_required_steps():
    body = open(os.path.join(ROOT, "install.sh"), encoding="utf-8").read()
    assert "brew install" in body                       # prerequisites
    assert "@tobilu/qmd" in body                         # qmd (exact package)
    assert "ruamel.yaml" in body                         # python deps
    assert "command -v claude" in body                   # detect-and-direct for claude
    assert "git clone" in body
    assert "scripts/trust_seed.py" in body and "seed" in body   # Inc 1 trust seed
    assert ".local/bin/magnolia" in body                 # magnolia on PATH
    assert "magnolia" in body.lower()                    # the closing instruction


def test_install_ps1_has_required_steps():
    p = os.path.join(ROOT, "install.ps1")
    assert os.path.isfile(p)
    body = open(p, encoding="utf-8").read()
    assert "winget install" in body                      # windows prerequisites
    assert "@tobilu/qmd" in body
    assert "git clone" in body
    assert "scripts/trust_seed.py" in body or "scripts\\trust_seed.py" in body
    assert "claude" in body                               # detect-and-direct
    assert "bin" in body                                  # adds repo bin to PATH
    assert "magnolia" in body.lower()
