"""trust_seed.py - detect Claude Code login/connectors and seed Layer-2 folder
trust for the Magnolia repo in ~/.claude.json. Pure stdlib, OS-agnostic (JSON
patch - no platform branches). Detection is read-only; seeding mutates only the
target projects[<abs path>] entry and preserves everything else.
"""
import json
import os
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
