"""trust_seed.py - detect Claude Code login/connectors and seed Layer-2 folder
trust for the Magnolia repo in ~/.claude.json. Pure stdlib, OS-agnostic (JSON
patch - no platform branches). Detection is read-only; seeding mutates only the
target projects[<abs path>] entry and preserves everything else.
"""
import json
import os
import shutil
import tempfile


def claude_config_path():
    """Mockable seam: absolute path to the user's ~/.claude.json."""
    return os.path.expanduser(os.path.join("~", ".claude.json"))


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_state(path=None):
    """Read-only detection. Returns {logged_in, connectors}. Absent/garbled
    config reads as a fresh, never-logged-in user."""
    data = _load(path or claude_config_path())
    if not isinstance(data, dict):
        return {"logged_in": False, "connectors": []}
    connectors = data.get("claudeAiMcpEverConnected") or []
    if not isinstance(connectors, list):
        connectors = []
    return {"logged_in": bool(data.get("oauthAccount")), "connectors": connectors}


def _atomic_write(path, data):
    """Atomically replace `path` with `data` as indented JSON.

    Preserves the target file's existing permission mode: mkstemp creates the
    temp at 0600, so without this copy the os.replace would silently downgrade
    an existing 0644 ~/.claude.json to 0600. A brand-new file (no existing
    target) keeps the default 0600.
    """
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        if os.path.exists(path):
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def seed_trust(project_path, path=None):
    """Seed Layer-2 folder trust for project_path. Mutates ONLY that project
    entry; preserves all other config. If ~/.claude.json is absent (login not
    done) we skip gracefully rather than fabricate it. Returns a result dict.

    Caveats - ~/.claude.json is owned and co-written by Claude Code itself:
    - This is a non-locking read-modify-write, so it must run when NO live
      interactive Claude Code session is writing the file (the installer's
      window: after `claude login`, before the board's first session). Full
      locking is intentionally deferred; worst case is a lost concurrent write.
    - It preserves the file's existing permission mode (see _atomic_write), so
      the auth material the file carries does not get its mode downgraded.
    """
    cfg_path = path or claude_config_path()
    data = _load(cfg_path)
    if not isinstance(data, dict):
        return {"status": "skipped", "reason": "no ~/.claude.json (run claude login first)"}
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(project_path, {})
    entry["hasTrustDialogAccepted"] = True
    entry["hasClaudeMdExternalIncludesApproved"] = True
    enabled = entry.get("enabledMcpjsonServers") or []
    if "qmd" not in enabled:
        enabled = enabled + ["qmd"]
    entry["enabledMcpjsonServers"] = enabled
    _atomic_write(cfg_path, data)
    return {"status": "seeded", "project": project_path}


def _main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="trust_seed")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("detect")
    d.add_argument("--path", default=None)
    s = sub.add_parser("seed")
    s.add_argument("project_path")
    s.add_argument("--path", default=None)
    args = p.parse_args(argv)
    if args.cmd == "detect":
        print(json.dumps(read_state(path=args.path)))
    else:
        print(json.dumps(seed_trust(args.project_path, path=args.path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
