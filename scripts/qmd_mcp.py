"""qmd_mcp - cross-platform stdio launcher for the qmd semantic-search MCP server.

.mcp.json is shared by macOS and Windows, so it can't name `qmd` directly: on
Windows npm installs qmd as qmd.cmd, which Claude Code can't spawn as a bare
command, and a `cmd /c` wrapper would break macOS. Instead .mcp.json runs this
script, which resolves qmd through platform_lib and becomes `qmd mcp` (plus any
extra argv) with stdio inherited untouched.

Nothing is ever written to stdout here: stdout is the MCP channel.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import platform_lib  # noqa: E402

MISSING_HINT = (
    "qmd (semantic search) is not installed or not on PATH, so the qmd MCP "
    "server can't start.\n"
    "Install it with:  npm install -g @tobilu/qmd   (needs Node >= 22)\n"
    "then run:         magnolia doctor\n"
)


def build_cmd(extra_args):
    """The command that runs `qmd mcp <extra_args>`, or None if qmd is missing."""
    exe = platform_lib.resolve_qmd()
    if not exe:
        return None
    return platform_lib.foreground_cmd(exe, ["mcp"] + list(extra_args))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        cmd = build_cmd(argv)
    except ValueError as e:
        sys.stderr.write(f"qmd_mcp: {e}\n")
        return 2
    if cmd is None:
        sys.stderr.write(MISSING_HINT)
        return 127
    return platform_lib.run_foreground(cmd)


if __name__ == "__main__":
    sys.exit(main())
