import pytest
import adapters
from adapters.project_management import jira as jira_adapter


def test_loader_returns_jira_module_for_jira_provider(profile_root):
    mod = adapters.get("project_management", root=profile_root)
    assert mod is jira_adapter


def test_loader_returns_none_when_provider_none(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: none\n")
    assert adapters.get("project_management", root=str(tmp_path)) is None


def test_jira_is_configured_true_for_populated_profile(profile_root):
    assert jira_adapter.is_configured(root=profile_root) is True


def test_jira_publish_delegates_to_publish_to_jira(profile_root, monkeypatch):
    captured = {}
    def fake_publish(draft, session_id=None):
        captured["draft"] = draft
        return ("ACM-1", "https://acme.atlassian.net/browse/ACM-1")
    import jira_publish
    monkeypatch.setattr(jira_publish, "publish_to_jira", fake_publish)
    key, url = jira_adapter.publish({"summary": "x", "type": "Bug"}, root=profile_root)
    assert key == "ACM-1"
    assert captured["draft"]["summary"] == "x"


def test_asana_stub_is_not_configured(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: asana\n")
    from adapters.project_management import asana
    assert asana.is_configured(root=str(tmp_path)) is False


def test_asana_publish_raises_not_configured(tmp_path):
    from adapters.project_management import asana
    from adapters.project_management._contract import NotConfigured
    with pytest.raises(NotConfigured):
        asana.publish({"summary": "x"}, root=str(tmp_path))


def test_transcript_loader_dispatches_otter(profile_root, monkeypatch):
    import profile_lib
    monkeypatch.setattr(profile_lib, "provider", lambda fam, root=None: "otter")
    mod = adapters.get("transcript", root=profile_root)
    from adapters.transcript import otter
    assert mod is otter


def test_granola_sync_delegates_to_runner(tmp_path, monkeypatch):
    # Real adapter delegates to transcript_sync._run_granola; the runner is
    # provider-gated so a bare tmp_path no-ops without firing a real fetch.
    from adapters.transcript import granola
    import transcript_sync
    called = {}
    monkeypatch.setattr(transcript_sync, "_run_granola",
                        lambda root: called.setdefault("ran", True))
    result = granola.sync(root=str(tmp_path))
    assert called.get("ran") is True
    assert result["status"] == "ok"
    assert result["provider"] == "granola"


def test_granola_sync_wraps_runner_failure(tmp_path, monkeypatch):
    from adapters.transcript import granola
    import transcript_sync
    def boom(root=None):
        raise RuntimeError("mcp unauthorized")
    monkeypatch.setattr(transcript_sync, "_run_granola", boom)
    result = granola.sync(root=str(tmp_path))
    assert result["status"] == "error"
    assert result["provider"] == "granola"
    assert "mcp unauthorized" in result["error"]
