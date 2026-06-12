"""Liveness gate on adapter routing (Adapt feature, Task 3).

An adapter owned by an OFF adaptation is invisible to routing - exactly like
"no provider configured". Liveness is the OUTER gate; Tier-2 (_is_confirmed /
NeedsConfirmation) is the INNER gate. They stay orthogonal: an off adapter
graceful-degrades (publish -> None) BEFORE any Tier-2 check runs.

Uses project_management/jira (real family/provider that imports cleanly via the
profile_root fixture). get() only imports the module - it never calls methods -
so no external creds are needed.
"""
import pytest
import adapters
from adapters.project_management import jira as jira_adapter


def test_get_returns_module_when_live(profile_root, monkeypatch):
    monkeypatch.setattr(adapters.adaptations_lib, "is_live", lambda *a, **k: True)
    assert adapters.get("project_management", root=profile_root) is jira_adapter


def test_get_returns_none_when_not_live(profile_root, monkeypatch):
    monkeypatch.setattr(adapters.adaptations_lib, "is_live", lambda *a, **k: False)
    assert adapters.get("project_management", root=profile_root) is None


def test_get_checks_liveness_with_adapter_surface_and_family_provider_ref(profile_root, monkeypatch):
    seen = {}
    def fake_is_live(surface, ref):
        seen["args"] = (surface, ref)
        return True
    monkeypatch.setattr(adapters.adaptations_lib, "is_live", fake_is_live)
    adapters.get("project_management", root=profile_root)
    assert seen["args"] == ("adapter", "project_management/jira")


def test_get_returns_none_when_provider_none_without_checking_liveness(tmp_path, monkeypatch):
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / "integrations.yaml").write_text(
        "project_management:\n  provider: none\n")
    def boom(*a, **k):
        raise AssertionError("is_live must not be called when provider is none")
    monkeypatch.setattr(adapters.adaptations_lib, "is_live", boom)
    assert adapters.get("project_management", root=str(tmp_path)) is None


def test_publish_graceful_degrades_for_off_adapter_before_tier2(profile_root, monkeypatch):
    # Owned-and-off adapter: publish returns None (graceful degrade) and the
    # Tier-2 _is_confirmed check is never reached (liveness is the outer gate).
    monkeypatch.setattr(adapters.adaptations_lib, "is_live", lambda *a, **k: False)
    def boom(*a, **k):
        raise AssertionError("_is_confirmed must not run when the adapter is off")
    monkeypatch.setattr(adapters, "_is_confirmed", boom)
    assert adapters.publish("project_management", {"summary": "x"}, root=profile_root) is None
