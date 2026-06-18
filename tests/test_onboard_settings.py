"""Tests for the onboarding fairway: settings file, pure fairway logic, and the
PreToolUse guard script.

The onboarding agent is HIGH-PRIVILEGE (it installs tools, runs auth, writes the
profile, invokes meta-onboard) - the OPPOSITE of the locked-down chat panel. The
--settings fairway hook is the REAL bound, so it must be valid, declare a
PreToolUse hook, and be shaped like adapt_settings.json (same keys).

Two layers under test (mirrors test_adapt_tools):
  1. scripts/onboard_tools.py - pure path/bash confinement logic.
  2. scripts/hooks/onboard_fairway_guard.py - the PreToolUse guard (invoked here
     with synthetic stdin payloads; no real `claude` needed).
"""
import json
import os
import subprocess
import sys

import onboard_tools

PM_OS_DIR = onboard_tools.PM_OS_DIR
SCRIPTS = os.path.join(PM_OS_DIR, "scripts")
SETTINGS = os.path.join(SCRIPTS, "hooks", "onboard_settings.json")
ADAPT_SETTINGS = os.path.join(SCRIPTS, "hooks", "adapt_settings.json")
GUARD = os.path.join(SCRIPTS, "hooks", "onboard_fairway_guard.py")


def _run_guard(payload):
    """Invoke the guard with a synthetic PreToolUse payload; return returncode."""
    res = subprocess.run(
        [sys.executable, GUARD],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    return res.returncode


# --- The settings file -------------------------------------------------------

def test_settings_is_valid_json():
    with open(SETTINGS) as f:
        json.load(f)


def test_settings_declares_a_pretooluse_hook():
    with open(SETTINGS) as f:
        data = json.load(f)
    assert "hooks" in data
    assert "PreToolUse" in data["hooks"]
    assert isinstance(data["hooks"]["PreToolUse"], list)
    assert data["hooks"]["PreToolUse"]


def test_settings_is_shaped_like_adapt_settings():
    with open(SETTINGS) as f:
        ours = json.load(f)
    with open(ADAPT_SETTINGS) as f:
        adapt = json.load(f)
    assert set(ours.keys()) == set(adapt.keys())
    entry = ours["hooks"]["PreToolUse"][0]
    adapt_entry = adapt["hooks"]["PreToolUse"][0]
    assert set(entry.keys()) == set(adapt_entry.keys())
    assert entry["hooks"][0]["type"] == adapt_entry["hooks"][0]["type"]


# --- Pure fairway logic ------------------------------------------------------

def test_write_inside_repo_is_in_fairway():
    assert onboard_tools.is_in_fairway("profile/profile.yaml") is True
    assert onboard_tools.is_in_fairway("datasets/meetings/x.md") is True


def test_write_outside_repo_is_not_in_fairway():
    assert onboard_tools.is_in_fairway("/etc/passwd") is False
    assert onboard_tools.is_in_fairway("../escape.txt") is False


def test_destructive_bash_is_denied():
    assert onboard_tools.bash_is_destructive("rm -rf /") is True
    assert onboard_tools.bash_is_destructive("rm -rf ~") is True
    assert onboard_tools.bash_is_destructive("sudo rm -rf /var") is True
    assert onboard_tools.bash_is_destructive(":(){ :|:& };:") is True


def test_legitimate_onboarding_bash_is_allowed():
    assert onboard_tools.bash_is_destructive("npm install -g @tobilu/qmd") is False
    assert onboard_tools.bash_is_destructive("python3 scripts/doctor.py detect") is False
    assert onboard_tools.bash_is_destructive("cp -R profile.example profile") is False
    assert onboard_tools.bash_is_destructive('mgc login --scopes "Mail.Send"') is False


# --- The guard script (deny=2, allow=0) --------------------------------------

def test_guard_allows_write_inside_repo():
    assert _run_guard({"tool_name": "Write",
                       "tool_input": {"file_path": "profile/profile.yaml"}}) == 0


def test_guard_denies_write_outside_repo():
    assert _run_guard({"tool_name": "Write",
                       "tool_input": {"file_path": "/etc/passwd"}}) == 2


def test_guard_denies_destructive_bash():
    assert _run_guard({"tool_name": "Bash",
                       "tool_input": {"command": "rm -rf /"}}) == 2


def test_guard_allows_installer_bash():
    assert _run_guard({"tool_name": "Bash",
                       "tool_input": {"command": "npm install -g @tobilu/qmd"}}) == 0


def test_guard_allows_non_write_tools():
    assert _run_guard({"tool_name": "Read",
                       "tool_input": {"file_path": "/etc/passwd"}}) == 0
