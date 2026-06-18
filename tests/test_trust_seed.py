import json
import os
import stat
import subprocess
import sys

import trust_seed


def test_read_state_missing_file(tmp_path):
    missing = tmp_path / "nope.json"
    st = trust_seed.read_state(path=str(missing))
    assert st == {"logged_in": False, "connectors": []}


def test_read_state_logged_in_with_connectors(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({
        "oauthAccount": {"accountUuid": "x"},
        "claudeAiMcpEverConnected": ["claude.ai Jira", "claude.ai Granola"],
    }))
    st = trust_seed.read_state(path=str(cfg))
    assert st["logged_in"] is True
    assert st["connectors"] == ["claude.ai Jira", "claude.ai Granola"]


def test_read_state_not_logged_in(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": None}))
    st = trust_seed.read_state(path=str(cfg))
    assert st["logged_in"] is False
    assert st["connectors"] == []


def test_seed_trust_creates_project_entry(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": {"accountUuid": "x"}, "projects": {}}))
    res = trust_seed.seed_trust("/repo/Magnolia", path=str(cfg))
    assert res["status"] == "seeded"
    data = json.loads(cfg.read_text())
    entry = data["projects"]["/repo/Magnolia"]
    assert entry["hasTrustDialogAccepted"] is True
    assert "qmd" in entry["enabledMcpjsonServers"]
    assert entry["hasClaudeMdExternalIncludesApproved"] is True


def test_seed_trust_preserves_existing_keys_and_other_projects(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({
        "oauthAccount": {"accountUuid": "x"},
        "theme": "dark",
        "projects": {
            "/other": {"hasTrustDialogAccepted": True},
            "/repo/Magnolia": {"lastCost": 1.23, "enabledMcpjsonServers": ["foo"]},
        },
    }))
    trust_seed.seed_trust("/repo/Magnolia", path=str(cfg))
    data = json.loads(cfg.read_text())
    assert data["theme"] == "dark"
    assert data["projects"]["/other"] == {"hasTrustDialogAccepted": True}
    entry = data["projects"]["/repo/Magnolia"]
    assert entry["lastCost"] == 1.23
    assert set(entry["enabledMcpjsonServers"]) == {"foo", "qmd"}


def test_seed_trust_idempotent(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": {"x": 1}, "projects": {}}))
    trust_seed.seed_trust("/repo/Magnolia", path=str(cfg))
    trust_seed.seed_trust("/repo/Magnolia", path=str(cfg))
    entry = json.loads(cfg.read_text())["projects"]["/repo/Magnolia"]
    assert entry["enabledMcpjsonServers"] == ["qmd"]


def test_seed_trust_skips_when_config_absent(tmp_path):
    missing = tmp_path / "nope.json"
    res = trust_seed.seed_trust("/repo/Magnolia", path=str(missing))
    assert res["status"] == "skipped"
    assert not missing.exists()


def test_seed_trust_preserves_file_mode(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": {"x": 1}, "projects": {}}))
    os.chmod(cfg, 0o644)
    trust_seed.seed_trust("/repo/Magnolia", path=str(cfg))
    mode = stat.S_IMODE(os.stat(cfg).st_mode)
    assert mode == 0o644


def test_cli_detect_prints_json(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": {"x": 1}, "claudeAiMcpEverConnected": ["claude.ai Jira"]}))
    script = os.path.join(os.path.dirname(trust_seed.__file__), "trust_seed.py")
    out = subprocess.check_output(
        [sys.executable, script, "detect", "--path", str(cfg)], text=True)
    parsed = json.loads(out)
    assert parsed["logged_in"] is True
    assert parsed["connectors"] == ["claude.ai Jira"]


def test_cli_seed_reports_status(tmp_path):
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"oauthAccount": {"x": 1}, "projects": {}}))
    script = os.path.join(os.path.dirname(trust_seed.__file__), "trust_seed.py")
    out = subprocess.check_output(
        [sys.executable, script, "seed", "/repo/Magnolia", "--path", str(cfg)], text=True)
    assert json.loads(out)["status"] == "seeded"
