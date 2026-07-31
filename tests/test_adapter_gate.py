import pytest
import adapters
from adapters.project_management import jira as jira_adapter


def _no_provider_root(tmp_path):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: none\n")
    return str(tmp_path)


def test_publish_returns_none_when_no_provider(tmp_path):
    assert adapters.publish("project_management", {"summary": "x"},
                            root=_no_provider_root(tmp_path)) is None


def test_publish_raises_needs_confirmation_when_explicitly_unconfirmed(profile_root):
    import profile_lib
    profile_lib.set_integration_confirmed("project_management", False, provider="jira", root=profile_root)
    with pytest.raises(adapters.NeedsConfirmation):
        adapters.publish("project_management", {"summary": "x", "type": "Bug"}, root=profile_root)


def test_publish_passes_when_confirmed(profile_root, monkeypatch):
    import profile_lib, jira_publish
    profile_lib.set_integration_confirmed("project_management", True, provider="jira", root=profile_root)
    monkeypatch.setattr(jira_publish, "publish_to_jira", lambda d, session_id=None: ("ACM-2", "u"))
    assert adapters.publish("project_management", {"summary": "x"}, root=profile_root) == ("ACM-2", "u")


def test_publish_grandfathers_configured_without_flag(profile_root, monkeypatch):
    # profile_root has jira creds but NO confirmed key -> grandfathered (creds = consent).
    import jira_publish
    monkeypatch.setattr(jira_publish, "publish_to_jira", lambda d, session_id=None: ("ACM-3", "u"))
    assert adapters.publish("project_management", {"summary": "x"}, root=profile_root) == ("ACM-3", "u")


def test_is_confirmed_explicit_false_blocks_even_if_configured(profile_root):
    import profile_lib
    profile_lib.set_integration_confirmed("project_management", False, provider="jira", root=profile_root)
    assert adapters._is_confirmed("project_management", jira_adapter, root=profile_root) is False
