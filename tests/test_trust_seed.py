import json
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
