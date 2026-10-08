import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_mcp_json_is_valid_and_portable():
    """The qmd MCP config must carry no per-operator absolute paths.

    cwd is not a supported .mcp.json field (Claude Code defaults the server's
    working dir to the launch dir, the repo root for the board's spawns). The
    command is the Python launcher (scripts/qmd_mcp.py), which resolves qmd via
    platform_lib - the interpreter defaults to PATH's python3 and can be pinned
    per machine with MAGNOLIA_PYTHON (install.ps1 sets it on Windows, where
    python3 is a .cmd shim Claude Code can't spawn). A hardcoded /Users/... or
    /opt/homebrew path would break any teammate who clones the repo.
    """
    for rel in (".mcp.json", ".claude/mcp.json"):
        data = json.loads((REPO / rel).read_text(encoding="utf-8"))
        qmd = data["mcpServers"]["qmd"]
        assert qmd["command"] == "${MAGNOLIA_PYTHON:-python3}", rel
        assert qmd["args"] == ["scripts/qmd_mcp.py"], rel
        assert "cwd" not in qmd, rel

        blob = (REPO / rel).read_text(encoding="utf-8")
        assert "/Users/jayjenkins" not in blob
        assert "/opt/homebrew" not in blob
