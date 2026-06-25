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


def test_install_sh_installs_only_missing_prereqs():
    body = open(os.path.join(ROOT, "install.sh"), encoding="utf-8").read()
    # No longer blanket-installs (which upgrades) - only what's missing.
    assert "brew install git node python pandoc" not in body
    assert "brew install $missing" in body               # word-split over missing list
    # Builds the missing list from individual command -v checks.
    assert 'missing="$missing git"' in body
    assert 'missing="$missing pandoc"' in body
    # qmd is skipped when already present.
    assert "command -v qmd" in body


def test_install_sh_redirects_stdin_for_interactive_commands():
    body = open(os.path.join(ROOT, "install.sh"), encoding="utf-8").read()
    # brew / npm / pip read </dev/null so they can't consume a piped script
    # and won't interactively prompt.
    assert "brew install $missing </dev/null" in body
    assert "npm install -g @tobilu/qmd </dev/null" in body
    assert "ruamel.yaml pytest </dev/null" in body
    # claude login reads the real terminal even under `... | bash`.
    assert "claude login </dev/tty" in body


def test_install_docs_use_safe_bash_c_oneliner():
    # The macOS/Linux one-liner must pass the script as an ARG to bash so stdin
    # stays the terminal (the sign-in step needs it) - not `curl | bash`.
    safe = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/jayhjenkins/Magnolia/main/install.sh)"'
    old = "install.sh | bash"
    for name in ("docs/INSTALL-macos.md", "docs/INSTALL-smoke-checklist.md"):
        body = open(os.path.join(ROOT, name), encoding="utf-8").read()
        assert safe in body, name
        assert old not in body, name
    # Windows command is intentionally unchanged (iex has no stdin footgun).
    win = open(os.path.join(ROOT, "docs/INSTALL-windows.md"), encoding="utf-8").read()
    assert "install.ps1 | iex" in win


def test_install_ps1_has_required_steps():
    p = os.path.join(ROOT, "install.ps1")
    assert os.path.isfile(p)
    body = open(p, encoding="utf-8").read()
    assert "winget install" in body                      # windows prerequisites
    assert "@tobilu/qmd" in body
    assert "git clone" in body
    assert "scripts/trust_seed.py" in body or "scripts\\trust_seed.py" in body
    assert "claude" in body                               # detect-and-direct
    assert "SetEnvironmentVariable" in body and '"Path"' in body  # User-PATH append logic
    assert "magnolia" in body.lower()
