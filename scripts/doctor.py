"""Magnolia Doctor — deterministic, side-effect-free capability DETECTION.

Writes profile/capabilities.json. Safe to run headless / on a cron. Remediation
is Claude-driven (see .claude/skills/workflow-doctor); this module never installs
anything or mutates external state — it only observes and records.
"""
import argparse
import importlib.util
import os
import shutil
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import platform_lib  # noqa: E402
import profile_lib  # noqa: E402
from send_message_graph import MGC_SCOPES  # noqa: E402 — the one canonical mgc scope set


def probe_which(name, remedy=None):
    found = shutil.which(name) is not None
    cap = {"kind": "local", "status": "ok" if found else "missing"}
    if not found and remedy:
        cap["remedy"] = remedy
    return cap


def _dep_missing(module):
    try:
        return importlib.util.find_spec(module) is None
    except ModuleNotFoundError:
        # A dotted name whose PARENT package is absent makes find_spec raise
        # rather than return None. The Doctor runs on unhealthy environments by
        # design, so treat that as "missing", never a crash.
        return True


def probe_python_deps(modules):
    missing = [m for m in modules if _dep_missing(m)]
    cap = {"kind": "local"}
    if missing:
        cap["status"] = "degraded"
        cap["missing"] = missing
    else:
        cap["status"] = "ok"
        cap["detail"] = ", ".join(modules)
    return cap


# Local CLI tools the Doctor knows how to detect (and a remedy hint for each).
# `recommended: True` means STRONGLY recommended, not throwaway-optional: it still
# won't BLOCK onboarding (required stays False), but its absence degrades real
# capability, so the Doctor pushes it with a plain-language `rationale`.
_LOCAL_TOOLS = {
    "python_interpreter": {
        "required": True,
        "detail": "Python runtime — required for all agent scripts (task.sh, task_cli.py, etc.)",
        "rationale": "without it, every background agent fails silently on start",
        "remedy": (
            "Windows: ensure Python is on Git Bash PATH — test with: python --version "
            "in the Claude Code terminal. If missing, rerun: "
            "winget install --id Python.Python.3.12 -e  then fully restart Claude Code. "
            "macOS: python3 should be on PATH; if not: brew install python3"
        ),
    },
    "qmd":        {"required": False, "recommended": True,
                   "detail": "semantic search across all your meetings, notes, and docs",
                   "rationale": "the killer feature — without it, search falls back to "
                                "keyword-only and quality drops",
                   # The ONE correct qmd is tobi/qmd (npm), NOT a brew formula or a
                   # look-alike repo — installing the wrong 'qmd' breaks the MCP.
                   "remedy": "npm install -g @tobilu/qmd  (needs Node >= 22; the correct "
                             "qmd is https://github.com/tobi/qmd — do NOT install a "
                             "different 'qmd'); it then runs as the qmd MCP via `qmd mcp`"},
    "pandoc":     {"required": False, "recommended": True,
                   "detail": "converts markdown <-> Word for doc sync / publish-package",
                   "rationale": "without it, creating/syncing Word docs (publish-package) won't work",
                   "remedy": "brew install pandoc (macOS) / "
                             "winget install --id JohnMacFarlane.Pandoc -e (Windows)"},
    "claude_cli": {"bin": "claude", "remedy": "see claude.ai/code install"},
    # No Homebrew formula/tap exists for the Microsoft Graph CLI — the official
    # route is the release binary from GitHub put on PATH (per
    # github.com/microsoftgraph/msgraph-cli and aka.ms/get/graphcli).
    "msgraph_cli":{"bin": "mgc", "required": False, "recommended": True,
                   "detail": "powers calendar invites + Outlook/Teams sends",
                   "rationale": "without it, messaging (Outlook + Teams send) and calendar "
                                "invites stay disabled",
                   "remedy": "download mgc from https://aka.ms/get/graphcli/latest/"
                             "osx-arm64.zip (osx-x64.zip Intel Mac, win-x64.zip Windows), "
                             "extract it, add the folder to your PATH; then authorize once: "
                             f'mgc login --scopes "{MGC_SCOPES}"'},
}
_PYTHON_DEPS = ["ruamel.yaml"]
# Remote connectors keyed by the integration category that implies them.
_REMOTE_FROM_INTEGRATION = {
    "project_management": lambda prov: prov,   # 'jira'/'asana'/'linear'
    "calendar": lambda prov: "m365" if prov == "m365" else prov,
    "messaging": lambda prov: "m365" if prov == "m365" else prov,   # Outlook + Teams sends
}


def probe_server(port):
    cap = {"kind": "service", "port": port}
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        cap["status"] = "running" if s.connect_ex(("127.0.0.1", port)) == 0 else "down"
    return cap


def probe_transcript(root=None):
    tc = profile_lib.transcript_config(root)
    provider = tc["provider"]
    cap = {"kind": "feed", "provider": provider, "target": tc["target"]}
    if provider == "none":
        cap["status"] = "not_expected"
        return cap
    state_dir = profile_lib.transcript_state_dir(root)
    if provider == "granola":
        # The Granola MCP is a claude.ai connector — detect() can't probe it
        # directly. A successful-sync marker (granola_downloaded.json) is our
        # proof of a working feed; absent it, nudge the user to connect.
        marker = os.path.join(state_dir, "granola_downloaded.json")
        if os.path.isfile(marker):
            cap["status"] = "ok"
        else:
            cap["status"] = "needs_reauth"
            cap["detail"] = "Connect Granola via /mcp, then finish granola.ai/mcp-signup"
        return cap
    # otter: a saved Playwright session.json means authed
    session = os.path.join(state_dir, "session.json")
    cap["status"] = "ok" if os.path.isfile(session) else "needs_reauth"
    return cap


def _remote_seeds(root=None):
    seeds = {}
    for category, namer in _REMOTE_FROM_INTEGRATION.items():
        prov = profile_lib.provider(category, root)
        if prov and prov != "none":
            seeds[namer(prov)] = {"kind": "remote", "expected": True, "status": "unknown"}
    return seeds


def detect(root=None):
    # Remote (claude.ai connector) status is stamped IN-SESSION by Claude — detect()
    # cannot probe it. Read the prior doc so we can carry forward any stamped status
    # instead of clobbering it back to "unknown" on every run.
    prior = profile_lib.read_capabilities(root).get("capabilities", {})
    caps = {}
    for name, spec in _LOCAL_TOOLS.items():
        binname = spec.get("bin", name)
        c = probe_which(binname, remedy=spec.get("remedy"))
        for key in ("required", "recommended", "detail", "rationale"):
            if key in spec:
                c[key] = spec[key]
        caps[name] = c
    # python_interpreter: prefer python3, fall back to python
    _py = shutil.which("python3") or shutil.which("python")
    from datetime import date as _date
    def _today(): return _date.today().isoformat()
    caps["python_interpreter"] = {
        **_LOCAL_TOOLS.get("python_interpreter", {}),
        "status": "ok" if _py else "missing",
        "binary": _py or "",
        "last_seen": _today() if _py else None,
    }
    caps["python_deps"] = probe_python_deps(_PYTHON_DEPS)
    caps["server"] = probe_server(profile_lib.server_port(root))
    caps["transcript"] = probe_transcript(root)
    for name, seed in _remote_seeds(root).items():
        prev = prior.get(name)
        if prev and prev.get("kind") == "remote" and prev.get("status") not in (None, "unknown"):
            seed = {**seed, "status": prev["status"]}
            if "last_seen" in prev:
                seed["last_seen"] = prev["last_seen"]
        caps[name] = seed
    doc = {
        "schema_version": profile_lib.CAPABILITIES_SCHEMA_VERSION,
        "platform": platform_lib.os_kind(),
        "capabilities": caps,
    }
    profile_lib.write_capabilities(doc, root)
    return doc


def report_text(caps):
    lines = [f"Magnolia Doctor — platform: {caps.get('platform', '?')}", ""]
    for name, c in sorted(caps.get("capabilities", {}).items()):
        status = c.get("status", "?")
        line = f"  {name:14} {status}"
        if status not in ("ok", "running") and c.get("recommended"):
            line += "   ★ STRONGLY RECOMMENDED"
            if c.get("rationale"):
                line += f" — {c['rationale']}"
        if c.get("remedy") and status not in ("ok", "running"):
            line += f"\n  {'':14} → {c['remedy']}"
        lines.append(line)
    return "\n".join(lines)


def check(cap_name, root=None):
    caps = profile_lib.read_capabilities(root)["capabilities"]
    c = caps.get(cap_name)
    if not c:
        return 2
    return 0 if c.get("status") in ("ok", "running") else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="doctor")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect")
    cp = sub.add_parser("check")
    cp.add_argument("capability")
    sub.add_parser("report")
    args = parser.parse_args(argv)

    if args.cmd == "detect":
        from datetime import datetime, timezone
        doc = detect()
        doc["generated_at"] = datetime.now(timezone.utc).isoformat()
        profile_lib.write_capabilities(doc)
        print(report_text(doc))
        return 0
    if args.cmd == "report":
        print(report_text(profile_lib.read_capabilities()))
        return 0
    if args.cmd == "check":
        return check(args.capability)


if __name__ == "__main__":
    sys.exit(main())
