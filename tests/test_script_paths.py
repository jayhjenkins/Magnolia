import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ["qmd-setup.sh", "qmd-nightly-update.sh", "run_task_server.sh"]


def test_no_hardcoded_pmos_paths():
    for name in SCRIPTS:
        text = (REPO / "scripts" / name).read_text()
        assert "/Users/jayjenkins/pm-os" not in text, f"{name}"
        assert '$HOME/pm-os' not in text and "$HOME/pm-os" not in text, f"{name}"


def test_qmd_scripts_resolve_qmd_from_path_first():
    # npm puts qmd wherever node's global prefix is (nvm, /usr/local, ...), not
    # always /opt/homebrew/bin - resolve it like the MCP launcher does.
    for name in ("qmd-setup.sh", "qmd-nightly-update.sh"):
        text = (REPO / "scripts" / name).read_text()
        assert "command -v qmd" in text, name
        calls = [l for l in text.splitlines()
                 if l.lstrip().startswith("/opt/homebrew/bin/qmd")]
        assert not calls, f"{name}: hardcoded qmd invocation {calls}"
