"""scripts/qmd_mcp.py - the cross-platform stdio launcher for the qmd MCP server."""
import json
import pathlib

import platform_lib
import qmd_mcp

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_build_cmd_default_is_qmd_mcp(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "darwin")
    monkeypatch.setattr(platform_lib, "resolve_qmd", lambda: "/opt/homebrew/bin/qmd")
    assert qmd_mcp.build_cmd([]) == ["/opt/homebrew/bin/qmd", "mcp"]


def test_build_cmd_passes_extra_argv_through(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "darwin")
    monkeypatch.setattr(platform_lib, "resolve_qmd", lambda: "/usr/local/bin/qmd")
    assert qmd_mcp.build_cmd(["--http", "--port", "8181"]) == [
        "/usr/local/bin/qmd", "mcp", "--http", "--port", "8181"]


def test_build_cmd_windows_cmd_shim(monkeypatch):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "windows")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\system32\cmd.exe")
    monkeypatch.setattr(platform_lib, "resolve_qmd",
                        lambda: r"C:\Users\pat\AppData\Roaming\npm\qmd.cmd")
    assert qmd_mcp.build_cmd([]) == (
        '"C:\\Windows\\system32\\cmd.exe" /d /s /c '
        '""C:\\Users\\pat\\AppData\\Roaming\\npm\\qmd.cmd" mcp"')


def test_build_cmd_missing_qmd_is_none(monkeypatch):
    monkeypatch.setattr(platform_lib, "resolve_qmd", lambda: None)
    assert qmd_mcp.build_cmd([]) is None


def test_main_missing_qmd_prints_ascii_hint_and_fails(monkeypatch, capsys):
    monkeypatch.setattr(platform_lib, "resolve_qmd", lambda: None)
    called = []
    monkeypatch.setattr(platform_lib, "run_foreground", lambda c: called.append(c))
    rc = qmd_mcp.main([])
    out, err = capsys.readouterr()
    assert rc != 0 and not called
    assert out == ""                      # stdout is the MCP channel: never pollute it
    assert "npm install -g @tobilu/qmd" in err and "magnolia doctor" in err
    err.encode("ascii")                   # ASCII-safe on a cp1252 console


def test_main_runs_child_and_returns_its_rc(monkeypatch, capsys):
    monkeypatch.setattr(platform_lib, "os_kind", lambda: "darwin")
    monkeypatch.setattr(platform_lib, "resolve_qmd", lambda: "/x/qmd")
    seen = []
    monkeypatch.setattr(platform_lib, "run_foreground", lambda c: seen.append(c) or 7)
    assert qmd_mcp.main(["--flag"]) == 7
    assert seen == [["/x/qmd", "mcp", "--flag"]]
    assert capsys.readouterr().out == ""


def test_mcp_json_never_spawns_bare_qmd():
    """Guard: a bare `qmd` command can't be spawned on Windows (npm makes qmd.cmd,
    which Claude Code won't run without cmd /c - and cmd /c breaks macOS). Both
    platforms go through the Python launcher instead."""
    for rel in (".mcp.json", ".claude/mcp.json"):
        data = json.loads((REPO / rel).read_text(encoding="utf-8"))
        qmd = data["mcpServers"]["qmd"]           # trust_seed enables it by this name
        assert qmd["command"] != "qmd", rel
        assert "cmd" not in qmd["command"].lower(), rel
        assert "python" in qmd["command"], rel
        assert qmd["args"] == ["scripts/qmd_mcp.py"], rel
        assert "cwd" not in qmd, rel
        blob = (REPO / rel).read_text(encoding="utf-8")
        assert "/Users/" not in blob and "/opt/homebrew" not in blob, rel
