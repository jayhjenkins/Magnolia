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
import time

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


def _tail(path, n=20):
    """Read the last n lines of a file. Best-effort; returns '' on error."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, 8192)
            if chunk == 0:
                return ""
            f.seek(-chunk, 2)
            data = f.read()
        lines = data.decode("utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


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
    if tc["external_feed"]:
        log_path = os.path.join(state_dir, "otter_sync.log")
        dl_path = os.path.join(state_dir, "downloaded.json")
        has_log = os.path.isfile(log_path)
        has_dl = os.path.isfile(dl_path)
        if not has_log and not has_dl:
            cap["status"] = "needs_reauth"
            return cap
        if has_log:
            try:
                age_h = (time.time() - os.path.getmtime(log_path)) / 3600
                if age_h > 48:
                    cap["status"] = "stale"
                    cap["detail"] = (f"otter_sync.log last modified "
                                     f"{age_h:.0f}h ago (>48h)")
                    cap["remedy"] = ("The transcript sync may be silently "
                                     "failing. Check logs/otter_sync.log.")
                    return cap
            except OSError:
                pass
            try:
                tail = _tail(log_path, 5)
                last_lines = tail.strip().splitlines()
                if last_lines and "ERROR" in last_lines[-1]:
                    cap["status"] = "degraded"
                    cap["detail"] = last_lines[-1][:120]
                    cap["remedy"] = "Check logs/otter_sync.log for details."
                    return cap
            except OSError:
                pass
        cap["status"] = "ok"
        return cap
    # otter: the feed needs the transcript extras (Playwright + otterai) installed
    # BEFORE otter_auth.py can run - it imports Playwright. Check deps first so the
    # Doctor surfaces "install the extras" rather than routing the user to a re-auth
    # script that crashes on import. Once the deps are present, a saved Playwright
    # session.json means authed.
    missing = [m for m in ("playwright", "otterai") if _dep_missing(m)]
    if missing:
        cap["status"] = "needs_setup"
        cap["missing"] = missing
        cap["remedy"] = ("install the Otter transcript extras: pip install -r "
                         "requirements-transcript.txt && python3 -m playwright install chromium")
        return cap
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
